from __future__ import annotations

import json
from typing import Any
from urllib.parse import urlencode, urlparse
from urllib.request import Request, urlopen

from momukbot.config import Settings
from momukbot.core.models import SearchCandidate
from momukbot.search.naver import (
    _allows_cafe_candidates,
    _candidate_category,
    _candidate_key,
    _dedupe,
    _is_excluded_general_candidate,
    _local_candidate_queries,
    _local_candidate_target_count,
    clean_html,
)


class KakaoNotConfigured(RuntimeError):
    pass


KAKAO_FOOD_CATEGORY_GROUP_CODE = "FD6"
KAKAO_CAFE_CATEGORY_GROUP_CODE = "CE7"
KAKAO_AMBIGUOUS_AREA_QUALIFIERS: dict[str, str] = {
    "서면": "부산 서면",
    "서면역": "부산 서면역",
}
KAKAO_AMBIGUOUS_AREA_REQUIRED_REGION_TERMS: dict[str, tuple[str, ...]] = {
    "서면": ("부산", "서면"),
    "서면역": ("부산", "서면"),
}
KAKAO_HAEJANG_GUKBAP_TERMS = ("돼지국밥", "순대국밥", "해장국", "국밥")


class KakaoLocalCandidateProvider:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    @property
    def configured(self) -> bool:
        return bool(self.settings.kakao_rest_api_key)

    def build_candidates(
        self,
        area: str,
        topic: str,
        count: int,
        context_hint: str = "",
        expanded: bool = False,
        initial_candidates: list[SearchCandidate] | None = None,
    ) -> list[SearchCandidate]:
        allow_cafe = _allows_cafe_candidates(topic, context_hint)
        category_group_code = kakao_category_group_code(topic, context_hint)
        candidates = [candidate for candidate in (initial_candidates or []) if is_kakao_place_url(candidate.url)]
        seen_candidates = {_candidate_key(candidate) for candidate in candidates}
        seen_queries = {candidate.query for candidate in candidates if candidate.query}
        queries = kakao_candidate_queries(area, topic, count, context_hint, expanded=expanded)
        target_count = _local_candidate_target_count(count, expanded=expanded)
        max_queries = min(len(queries), max(1, (target_count + 14) // 15))
        for query in queries[:max_queries]:
            if query in seen_queries:
                continue
            seen_queries.add(query)
            local = self.search_keyword(query, size=15, category_group_code=category_group_code)
            selected_region, region_candidates = kakao_same_name_regions(local)
            if not kakao_selected_region_matches_area(area, selected_region):
                continue
            documents = local.get("documents") if isinstance(local, dict) else []
            if not isinstance(documents, list):
                continue
            for document in documents:
                if not isinstance(document, dict):
                    continue
                candidate = candidate_from_kakao_document(
                    document,
                    query,
                    selected_region=selected_region,
                    region_candidates=region_candidates,
                )
                if candidate is None or not is_kakao_place_url(candidate.url):
                    continue
                key = _candidate_key(candidate)
                if not key or key in seen_candidates:
                    continue
                if not allow_cafe and _is_excluded_general_candidate(candidate):
                    continue
                seen_candidates.add(key)
                candidates.append(candidate)
                if len(candidates) >= target_count:
                    return candidates
        return candidates

    def search_keyword(
        self,
        query: str,
        size: int = 15,
        page: int = 1,
        category_group_code: str = "",
    ) -> dict[str, Any]:
        if not self.configured:
            raise KakaoNotConfigured("KAKAO_REST_API_KEY is not configured")
        params: dict[str, str | int] = {
            "query": query,
            "size": max(1, min(size, 15)),
            "page": max(1, min(page, 45)),
        }
        if category_group_code:
            params["category_group_code"] = category_group_code
        url = "https://dapi.kakao.com/v2/local/search/keyword.json?" + urlencode(params)
        req = Request(url, method="GET")
        req.add_header("Authorization", f"KakaoAK {self.settings.kakao_rest_api_key}")
        with urlopen(req, timeout=20) as resp:
            return json.loads(resp.read().decode("utf-8"))

    def check_connection(self) -> None:
        self.search_keyword("서울 맛집", size=1, category_group_code=KAKAO_FOOD_CATEGORY_GROUP_CODE)


def candidate_from_kakao_document(
    document: dict[str, Any],
    query: str,
    selected_region: str = "",
    region_candidates: tuple[str, ...] = (),
) -> SearchCandidate | None:
    name = clean_html(str(document.get("place_name") or ""))
    if not name:
        return None
    raw_category = clean_html(str(document.get("category_name") or ""))
    address = clean_html(str(document.get("road_address_name") or document.get("address_name") or ""))
    url = str(document.get("place_url") or "").strip()
    return SearchCandidate(
        name=name,
        category=_candidate_category(name, raw_category),
        raw_category=raw_category,
        address=address,
        url=url,
        source="kakao_local",
        query=query,
        place_id=str(document.get("id") or "").strip(),
        phone=clean_html(str(document.get("phone") or "")),
        category_group_code=str(document.get("category_group_code") or "").strip(),
        category_group_name=clean_html(str(document.get("category_group_name") or "")),
        x=str(document.get("x") or "").strip(),
        y=str(document.get("y") or "").strip(),
        distance=str(document.get("distance") or "").strip(),
        selected_region=selected_region,
        region_candidates=region_candidates,
    )


def is_kakao_place_url(url: str) -> bool:
    parsed = urlparse(str(url or "").strip())
    if parsed.scheme not in ("http", "https"):
        return False
    return parsed.netloc.lower() == "place.map.kakao.com"


def kakao_category_group_code(topic: str, context_hint: str = "") -> str:
    if _allows_cafe_candidates(topic, context_hint):
        return KAKAO_CAFE_CATEGORY_GROUP_CODE
    return KAKAO_FOOD_CATEGORY_GROUP_CODE


def kakao_candidate_queries(
    area: str,
    topic: str,
    count: int,
    context_hint: str = "",
    expanded: bool = False,
) -> list[str]:
    area = area.strip()
    topic = topic.strip()
    queries: list[str] = []
    if _is_haejang_gukbap_intent(topic, context_hint):
        for query_area in _kakao_query_areas(area):
            queries.extend(" ".join([query_area, term]).strip() for term in KAKAO_HAEJANG_GUKBAP_TERMS)
    else:
        for query_area in _kakao_query_areas(area):
            if query_area == area:
                continue
            queries.extend(_local_candidate_queries(query_area, topic, count, context_hint, expanded=expanded))
    queries.extend(_local_candidate_queries(area, topic, count, context_hint, expanded=expanded))
    return _dedupe([query for query in queries if query])


def kakao_same_name_regions(response: dict[str, Any]) -> tuple[str, tuple[str, ...]]:
    if not isinstance(response, dict):
        return "", ()
    meta = response.get("meta")
    if not isinstance(meta, dict):
        return "", ()
    same_name = meta.get("same_name")
    if not isinstance(same_name, dict):
        return "", ()
    selected_region = clean_html(str(same_name.get("selected_region") or ""))
    raw_regions = same_name.get("region")
    regions: list[str] = []
    if isinstance(raw_regions, list):
        regions = [clean_html(str(region or "")) for region in raw_regions]
    return selected_region, tuple(region for region in regions if region)


def kakao_selected_region_matches_area(area: str, selected_region: str) -> bool:
    area_key = _compact_area(area)
    region_key = _compact_area(selected_region)
    if not area_key or not region_key:
        return True
    required_terms = KAKAO_AMBIGUOUS_AREA_REQUIRED_REGION_TERMS.get(area_key)
    if required_terms:
        return all(term in selected_region for term in required_terms)
    if area_key in region_key or region_key in area_key:
        return True
    variants = {area_key}
    for suffix in ("역", "동", "면", "읍", "리", "구", "시", "군"):
        if area_key.endswith(suffix) and len(area_key) > len(suffix):
            variants.add(area_key[: -len(suffix)])
    return any(len(variant) >= 2 and variant in region_key for variant in variants)


def _kakao_query_areas(area: str) -> list[str]:
    area = area.strip()
    if not area:
        return [area]
    qualified = KAKAO_AMBIGUOUS_AREA_QUALIFIERS.get(_compact_area(area))
    if not qualified:
        return [area]
    return _dedupe([qualified, area])


def _is_haejang_gukbap_intent(topic: str, context_hint: str = "") -> bool:
    text = " ".join([topic, context_hint])
    return any(term in text for term in ("해장", "국밥", "순대국", "순댓국", "돼지국밥", "감자탕"))


def _compact_area(area: str) -> str:
    return "".join(str(area or "").split())
