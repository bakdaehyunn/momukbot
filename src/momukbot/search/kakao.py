from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode, urlparse
from urllib.request import Request, urlopen

from momukbot.config import Settings
from momukbot.core.models import RequestLocation, SearchCandidate
from momukbot.search.candidates import (
    allows_cafe_candidates,
    allows_fast_food_candidates,
    candidate_category,
    candidate_key,
    clean_html,
    dedupe_strings,
    is_excluded_general_candidate,
    local_candidate_queries,
    local_candidate_target_count,
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
LOCATION_EXTRA_QUERY_LIMIT = 4


@dataclass(frozen=True)
class KakaoLocationContext:
    area_label: str
    query_areas: tuple[str, ...] = ()


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
        location: RequestLocation | None = None,
        location_query_areas: tuple[str, ...] = (),
    ) -> list[SearchCandidate]:
        allow_cafe = allows_cafe_candidates(topic, context_hint)
        allow_fast_food = allows_fast_food_candidates(topic, context_hint)
        category_group_code = kakao_category_group_code(topic, context_hint)
        candidates = [candidate for candidate in (initial_candidates or []) if is_kakao_place_url(candidate.url)]
        seen_candidates = {candidate_key(candidate) for candidate in candidates}
        seen_queries = {candidate.query for candidate in candidates if candidate.query}
        if location:
            queries = kakao_location_candidate_queries(
                area,
                topic,
                count,
                context_hint,
                expanded=expanded,
                query_areas=location_query_areas,
            )
        else:
            queries = kakao_candidate_queries(area, topic, count, context_hint, expanded=expanded)
        target_count = local_candidate_target_count(count, expanded=expanded)
        max_queries = max(1, (target_count + 14) // 15)
        if location:
            max_queries += LOCATION_EXTRA_QUERY_LIMIT
            representative_query_floor = len(
                _coordinate_centered_candidate_queries(topic, count, context_hint, expanded=expanded)
            ) + len(dedupe_strings([*location_query_areas, *_location_query_areas(area)]))
            max_queries = max(max_queries, representative_query_floor)
        max_queries = min(len(queries), max_queries)
        for query in queries[:max_queries]:
            if query in seen_queries:
                continue
            seen_queries.add(query)
            local = self.search_keyword(
                query,
                size=15,
                category_group_code=category_group_code,
                location=location,
                sort="distance" if location else "",
            )
            selected_region, region_candidates = kakao_same_name_regions(local)
            if not location and not kakao_selected_region_matches_area(area, selected_region):
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
                key = candidate_key(candidate)
                if not key or key in seen_candidates:
                    continue
                if not allow_cafe and is_excluded_general_candidate(candidate, allow_fast_food=allow_fast_food):
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
        location: RequestLocation | None = None,
        sort: str = "",
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
        if location:
            params["x"] = f"{location.longitude:.7f}"
            params["y"] = f"{location.latitude:.7f}"
            params["radius"] = max(1, min(location.radius_m, 20000))
        if sort:
            params["sort"] = sort
        url = "https://dapi.kakao.com/v2/local/search/keyword.json?" + urlencode(params)
        req = Request(url, method="GET")
        req.add_header("Authorization", f"KakaoAK {self.settings.kakao_rest_api_key}")
        with urlopen(req, timeout=20) as resp:
            return json.loads(resp.read().decode("utf-8"))

    def check_connection(self) -> None:
        self.search_keyword("서울 맛집", size=1, category_group_code=KAKAO_FOOD_CATEGORY_GROUP_CODE)

    def location_label(self, location: RequestLocation) -> str:
        return self.location_context(location).area_label

    def location_context(self, location: RequestLocation) -> KakaoLocationContext:
        if location.label.strip():
            label = location.label.strip()
            return KakaoLocationContext(area_label=label, query_areas=tuple(_location_query_areas(label)))
        response = self.coord_to_address(location)
        return kakao_location_context_from_response(response)

    def coord_to_address(self, location: RequestLocation) -> dict[str, Any]:
        if not self.configured:
            raise KakaoNotConfigured("KAKAO_REST_API_KEY is not configured")
        params = {
            "x": f"{location.longitude:.7f}",
            "y": f"{location.latitude:.7f}",
        }
        url = "https://dapi.kakao.com/v2/local/geo/coord2address.json?" + urlencode(params)
        req = Request(url, method="GET")
        req.add_header("Authorization", f"KakaoAK {self.settings.kakao_rest_api_key}")
        with urlopen(req, timeout=20) as resp:
            return json.loads(resp.read().decode("utf-8"))


def kakao_location_context_from_response(response: dict[str, Any]) -> KakaoLocationContext:
    if not isinstance(response, dict):
        return KakaoLocationContext(area_label="현재 위치")
    documents = response.get("documents")
    if not isinstance(documents, list) or not documents:
        return KakaoLocationContext(area_label="현재 위치")
    document = documents[0]
    if not isinstance(document, dict):
        return KakaoLocationContext(area_label="현재 위치")
    address = document.get("address")
    if not isinstance(address, dict):
        address = document.get("road_address")
    area_parts: list[str] = []
    if isinstance(address, dict):
        area_parts = [
            clean_html(str(address.get("region_1depth_name") or "")),
            clean_html(str(address.get("region_2depth_name") or "")),
            clean_html(str(address.get("region_3depth_name") or "")),
        ]
    area_label = " ".join(part for part in area_parts if part).strip() or "현재 위치"
    query_areas = _location_query_areas(area_label)
    road_address = document.get("road_address")
    if isinstance(road_address, dict):
        road_name = clean_html(str(road_address.get("road_name") or ""))
        road_parts = [
            clean_html(str(road_address.get("region_1depth_name") or "")),
            clean_html(str(road_address.get("region_2depth_name") or "")),
            road_name,
        ]
        road_label = " ".join(part for part in road_parts if part).strip()
        query_areas.extend(part for part in (road_label, road_name) if part)
    return KakaoLocationContext(area_label=area_label, query_areas=tuple(dedupe_strings(query_areas)))


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
        category=candidate_category(name, raw_category),
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
    if allows_cafe_candidates(topic, context_hint):
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
            queries.extend(local_candidate_queries(query_area, topic, count, context_hint, expanded=expanded))
    queries.extend(local_candidate_queries(area, topic, count, context_hint, expanded=expanded))
    return dedupe_strings([query for query in queries if query])


def kakao_location_candidate_queries(
    area: str,
    topic: str,
    count: int,
    context_hint: str = "",
    expanded: bool = False,
    query_areas: tuple[str, ...] = (),
) -> list[str]:
    queries = _coordinate_centered_candidate_queries(topic, count, context_hint, expanded=expanded)
    location_areas = dedupe_strings([*query_areas, *_location_query_areas(area)])
    representative_queries = _representative_location_candidate_queries(location_areas, topic, count, context_hint)
    queries.extend(representative_queries)
    for query_area in location_areas:
        queries.extend(
            query
            for query in kakao_candidate_queries(query_area, topic, count, context_hint, expanded=expanded)
            if query not in representative_queries
        )
    return dedupe_strings([query for query in queries if query])


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
    return dedupe_strings([qualified, area])


def _location_query_areas(area: str) -> list[str]:
    parts = [part for part in area.strip().split() if part]
    if not parts:
        return []
    variants = [area.strip()]
    if len(parts) >= 3:
        variants.append(parts[-1])
    return dedupe_strings([variant for variant in variants if variant and variant != "현재 위치"])


def _coordinate_centered_candidate_queries(
    topic: str,
    count: int,
    context_hint: str = "",
    expanded: bool = False,
) -> list[str]:
    topic = topic.strip()
    if _is_haejang_gukbap_intent(topic, context_hint):
        queries = list(KAKAO_HAEJANG_GUKBAP_TERMS)
    elif allows_cafe_candidates(topic, context_hint):
        queries = ["카페", "커피", "디저트", "베이커리"]
    elif topic and topic != "맛집":
        queries = local_candidate_queries("", topic, count, context_hint, expanded=expanded)
    else:
        queries = ["맛집", "식당", "밥집", "한식"]
        if expanded:
            queries.extend(["일식", "중식", "양식", "분식", "국밥", "고기", "술집"])
    return dedupe_strings([query.strip() for query in queries if query.strip()])[:4]


def _representative_location_candidate_queries(
    query_areas: list[str],
    topic: str,
    count: int,
    context_hint: str = "",
) -> list[str]:
    queries: list[str] = []
    for query_area in query_areas:
        area_queries = kakao_candidate_queries(query_area, topic, count, context_hint)
        if area_queries:
            queries.append(area_queries[0])
    return dedupe_strings(queries)


def _is_haejang_gukbap_intent(topic: str, context_hint: str = "") -> bool:
    text = " ".join([topic, context_hint])
    return any(term in text for term in ("해장", "국밥", "순대국", "순댓국", "돼지국밥", "감자탕"))


def _compact_area(area: str) -> str:
    return "".join(str(area or "").split())
