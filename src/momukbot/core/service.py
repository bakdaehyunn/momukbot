from __future__ import annotations

import logging
import re
import time
from datetime import datetime
from urllib.parse import urlparse

from momukbot.agent.base import AgentProvider
from momukbot.config import Settings
from momukbot.core.formatter import (
    filter_preferred_links,
    format_recommendation_message,
    normalize_name,
)
from momukbot.core.json_utils import extract_json_object
from momukbot.core.models import (
    ParsedRequest,
    RecommendationItem,
    RecommendationResult,
    SearchCandidate,
    SearchContext,
)
from momukbot.core.parser import parse_request
from momukbot.core.prompts import recommendation_prompt
from momukbot.search.base import SearchProvider
from momukbot.storage.sqlite import RecommendationStore


CAFE_INTENT_TERMS = ("카페", "커피", "커피집", "디저트", "베이커리", "빵")
GENERAL_EXCLUDED_NAME_WORDS = (
    "스타벅스",
    "이디야",
    "메가커피",
    "컴포즈커피",
    "투썸",
    "빽다방",
    "맥도날드",
    "버거킹",
    "롯데리아",
    "써브웨이",
    "서브웨이",
    "맘스터치",
    "kfc",
    "파파이스",
    "노브랜드버거",
)
GENERAL_EXCLUDED_CATEGORY_WORDS = (
    "카페",
    "커피",
    "디저트",
    "베이커리",
    "제과",
    "제빵",
    "도넛",
    "아이스크림",
    "패스트푸드",
    "브런치카페",
)


class RecommendationService:
    def __init__(
        self,
        settings: Settings,
        agent: AgentProvider,
        search_provider: SearchProvider,
        store: RecommendationStore | None = None,
        logger: logging.Logger | None = None,
    ) -> None:
        self.settings = settings
        self.agent = agent
        self.search_provider = search_provider
        self.store = store
        self.logger = logger or logging.getLogger("momukbot.telegram")

    def parse(self, text: str) -> ParsedRequest:
        return parse_request(text, default_count=self.settings.default_count)

    def handle_text(self, chat_id: str, text: str, dry_run: bool = False) -> str | None:
        total_start = time.monotonic()
        stage_start = time.monotonic()
        parsed = self.parse(text)
        self._log_stage(
            chat_id,
            "parse",
            time.monotonic() - stage_start,
            intent=parsed.intent,
            has_area=bool(parsed.area),
            count=parsed.count,
        )
        if parsed.intent == "unknown":
            self._log_stage(chat_id, "total", time.monotonic() - total_start, result="ignored")
            return None
        if not parsed.area:
            self._log_stage(chat_id, "total", time.monotonic() - total_start, result="missing_area")
            return "지역을 못 찾았어요. 예: `서면에서 해장 국밥 추천해줘`처럼 지역을 포함해서 보내주세요."
        return self.recommend(
            chat_id=chat_id,
            request_text=text,
            parsed=parsed,
            dry_run=dry_run,
            started_at=total_start,
        )

    def recommend(
        self,
        chat_id: str,
        request_text: str,
        parsed: ParsedRequest,
        dry_run: bool = False,
        started_at: float | None = None,
    ) -> str:
        total_start = started_at or time.monotonic()
        stage_start = time.monotonic()
        parsed = ParsedRequest(
            intent=parsed.intent,
            area=parsed.area,
            topic=parsed.topic,
            meal_type=parsed.meal_type,
            budget=parsed.budget,
            occasion=parsed.occasion,
            count=max(1, min(30, parsed.count or self.settings.default_count)),
        )
        self._log_stage(
            chat_id,
            "normalize",
            time.monotonic() - stage_start,
            count=parsed.count,
            has_topic=bool(parsed.topic),
        )
        context_hint = ", ".join(
            item for item in [parsed.meal_type, parsed.budget, parsed.occasion] if item
        )
        stage_start = time.monotonic()
        try:
            search_context = self.search_provider.build_context(
                parsed.area,
                parsed.topic,
                parsed.count,
                context_hint=context_hint,
            )
        except Exception:
            self._log_stage(
                chat_id,
                "search_context",
                time.monotonic() - stage_start,
                failed=True,
            )
            self._log_stage(
                chat_id,
                "total",
                time.monotonic() - total_start,
                result="failed",
                failed_stage="search_context",
            )
            raise
        self._log_stage(
            chat_id,
            "search_context",
            time.monotonic() - stage_start,
            provider=search_context.used_provider or "",
            configured=search_context.configured,
            quota_blocked=search_context.quota_blocked,
            evidence_available=search_context.evidence_available,
            candidate_count=len(search_context.candidates),
            context_chars=len(search_context.text),
        )
        if not search_context.evidence_available:
            response = _naver_evidence_unavailable_response(search_context)
            self._log_stage(
                chat_id,
                "total",
                time.monotonic() - total_start,
                result="naver_evidence_unavailable",
                configured=search_context.configured,
                quota_blocked=search_context.quota_blocked,
                result_chars=len(response),
            )
            return response
        stage_start = time.monotonic()
        prompt = recommendation_prompt(
            parsed,
            datetime.now(),
            naver_context=search_context.text,
            request_text=request_text,
        )
        self._log_stage(
            chat_id,
            "prompt_build",
            time.monotonic() - stage_start,
            prompt_chars=len(prompt),
        )
        if dry_run:
            stage_start = time.monotonic()
            response = self._format_dry_run(parsed, search_context, prompt)
            self._log_stage(
                chat_id,
                "format",
                time.monotonic() - stage_start,
                result_chars=len(response),
                dry_run=True,
            )
            self._log_stage(
                chat_id,
                "total",
                time.monotonic() - total_start,
                result="dry_run",
                result_chars=len(response),
            )
            return response

        stage_start = time.monotonic()
        try:
            raw = self.agent.generate(prompt)
        except Exception:
            self._log_stage(
                chat_id,
                "agent_generate",
                time.monotonic() - stage_start,
                failed=True,
            )
            self._log_stage(
                chat_id,
                "total",
                time.monotonic() - total_start,
                result="failed",
                failed_stage="agent_generate",
            )
            raise
        self._log_stage(
            chat_id,
            "agent_generate",
            time.monotonic() - stage_start,
            raw_chars=len(raw),
        )
        stage_start = time.monotonic()
        result = parse_recommendation(raw, self.settings.blog_allowed_domains)
        self._log_stage(
            chat_id,
            "response_parse",
            time.monotonic() - stage_start,
            item_count=len(result.items),
            has_json=result.raw_json is not None,
        )
        confirmed_blog_evidence = _confirmed_blog_evidence(
            search_context.text,
            self.settings.blog_allowed_domains,
        )
        initial_item_count = len(result.items)
        filled_count = _reconcile_result_items(
            parsed,
            result,
            confirmed_blog_evidence,
            search_context.candidates,
        )
        if len(result.items) != initial_item_count or filled_count:
            self._log_stage(
                chat_id,
                "result_filter",
                0,
                initial_item_count=initial_item_count,
                item_count=len(result.items),
                removed_count=max(0, initial_item_count - len(result.items) + filled_count),
                filled_count=filled_count,
                confirmed_blog_url_count=len(confirmed_blog_evidence),
            )
        if not result.items and result.raw_json is None and result.raw_text:
            response = "추천 결과 형식을 정리하지 못했어요. 잠시 후 다시 시도해주세요."
            self._log_stage(
                chat_id,
                "total",
                time.monotonic() - total_start,
                result="invalid_agent_response",
                result_chars=len(response),
            )
            return response
        partial_notice = ""
        if len(result.items) < parsed.count:
            if result.items:
                partial_notice = (
                    f"네이버 블로그 근거가 확인된 {len(result.items)}곳만 보여드려요. "
                    f"요청한 {parsed.count}곳 중 확인되지 않은 후보는 제외했습니다.\n\n"
                )
                self._log_stage(
                    chat_id,
                    "confirmed_partial",
                    0,
                    item_count=len(result.items),
                    target_count=parsed.count,
                    confirmed_blog_url_count=len(confirmed_blog_evidence),
                )
            else:
                response = (
                    "네이버 블로그 근거가 확인된 후보를 찾지 못했어요. "
                    "다른 지역이나 더 넓은 요청으로 다시 시도해주세요."
                )
                self._log_stage(
                    chat_id,
                    "total",
                    time.monotonic() - total_start,
                    result="no_confirmed_blog_evidence",
                    item_count=len(result.items),
                    target_count=parsed.count,
                    confirmed_blog_url_count=len(confirmed_blog_evidence),
                    result_chars=len(response),
                )
                return response
        if not result.items:
            response = "이번 요청에서는 추천할 후보를 찾지 못했습니다."
            self._log_stage(
                chat_id,
                "total",
                time.monotonic() - total_start,
                result="empty_result",
                item_count=len(result.items),
                result_chars=len(response),
            )
            return response
        _attach_map_candidates(result.items, search_context.candidates)
        raw_to_store = raw if self.settings.store_raw_response else ""
        stage_start = time.monotonic()
        if self.store:
            self.store.add_result(
                chat_id=chat_id,
                request_text=request_text,
                area=parsed.area,
                topic=parsed.topic,
                search_keyword=result.search_keyword,
                raw_response=raw_to_store,
                items=result.items,
            )
        self._log_stage(
            chat_id,
            "store",
            time.monotonic() - stage_start,
            store_enabled=self.store is not None,
            raw_stored=bool(raw_to_store),
            item_count=len(result.items),
        )
        stage_start = time.monotonic()
        response = partial_notice + format_recommendation_message(
            result.search_keyword,
            result.items,
            area=parsed.area,
            decision_criteria=result.decision_criteria,
            top_summary=result.top_summary,
        )
        self._log_stage(
            chat_id,
            "format",
            time.monotonic() - stage_start,
            result_chars=len(response),
            item_count=len(result.items),
        )
        self._log_stage(
            chat_id,
            "total",
            time.monotonic() - total_start,
            result="ok",
            result_chars=len(response),
            item_count=len(result.items),
            partial=bool(partial_notice),
        )
        return response

    def _format_dry_run(self, parsed: ParsedRequest, search_context: SearchContext, prompt: str) -> str:
        lines = [
            "dry-run: 실제 AI 에이전트 호출은 하지 않았습니다.",
            f"area={parsed.area}",
            f"topic={parsed.topic or '(empty)'}",
            f"count={parsed.count}",
            f"search_provider={search_context.used_provider or '(none)'}",
            f"search_configured={search_context.configured}",
            f"quota_blocked={search_context.quota_blocked}",
            f"evidence_available={search_context.evidence_available}",
            f"candidate_count={len(search_context.candidates)}",
            f"context_chars={len(search_context.text)}",
            "",
            "prompt_preview:",
            prompt[:9000],
        ]
        return "\n".join(lines)

    def _log_stage(self, chat_id: str, stage: str, elapsed: float, **fields: object) -> None:
        if not self.logger.isEnabledFor(logging.INFO):
            return
        suffix = _format_log_fields(fields)
        if suffix:
            suffix = " " + suffix
        self.logger.info(
            "recommendation stage chat_id=%s stage=%s elapsed=%.2fs%s",
            _mask_identifier(chat_id),
            stage,
            elapsed,
            suffix,
        )


def parse_recommendation(
    raw: str,
    allowed_domains: tuple[str, ...] = ("blog.naver.com",),
) -> RecommendationResult:
    data = extract_json_object(raw)
    if not data:
        return RecommendationResult(raw_text=raw)
    keyword = str(data.get("search_keyword") or "").strip()
    decision_criteria = _string_list(data.get("decision_criteria"), limit=5)
    top_summary = str(data.get("top_summary") or "").strip()
    raw_items = data.get("items")
    items: list[RecommendationItem] = []
    seen: set[str] = set()
    if isinstance(raw_items, list):
        for raw_item in raw_items:
            if not isinstance(raw_item, dict):
                continue
            name = str(raw_item.get("name") or "").strip()
            key = normalize_name(name)
            if not name or not key or key in seen:
                continue
            seen.add(key)
            links = raw_item.get("links")
            clean_links: list[dict[str, str]] = []
            if isinstance(links, list):
                for link in links:
                    if not isinstance(link, dict):
                        continue
                    clean_links.append(
                        {
                            "label": str(link.get("label") or "링크").strip(),
                            "url": str(link.get("url") or "").strip(),
                        }
                    )
            items.append(
                RecommendationItem(
                    name=name,
                    category=str(raw_item.get("category") or "").strip(),
                    status_marker=str(raw_item.get("status_marker") or "영업시간 미확인").strip(),
                    reason=str(raw_item.get("reason") or "").strip(),
                    links=filter_preferred_links(clean_links, allowed_domains),
                    fit_tags=_string_list(raw_item.get("fit_tags"), limit=4),
                    tradeoff=str(raw_item.get("tradeoff") or "").strip(),
                    intent_fit=_bounded_int(raw_item.get("intent_fit"), minimum=0, maximum=5),
                    meal_fit=_bounded_int(raw_item.get("meal_fit"), minimum=0, maximum=5),
                    occasion_fit=_bounded_int(raw_item.get("occasion_fit"), minimum=0, maximum=5),
                    risk_flags=_string_list(raw_item.get("risk_flags"), limit=4),
                )
            )
    return RecommendationResult(
        search_keyword=keyword,
        items=items,
        decision_criteria=decision_criteria,
        top_summary=top_summary,
        raw_text=raw,
        raw_json=data,
    )


def _string_list(value: object, limit: int) -> list[str]:
    if not isinstance(value, list):
        return []
    items: list[str] = []
    for item in value:
        text = str(item or "").strip()
        if text:
            items.append(text)
        if len(items) >= limit:
            break
    return items


def _bounded_int(value: object, minimum: int, maximum: int) -> int:
    if isinstance(value, bool):
        return minimum
    try:
        number = int(value)
    except (TypeError, ValueError):
        return minimum
    return max(minimum, min(maximum, number))


def _filter_result_items(
    parsed: ParsedRequest,
    result: RecommendationResult,
    confirmed_blog_evidence: dict[str, str],
) -> None:
    items = [item for item in result.items if _has_confirmed_blog_link(item, confirmed_blog_evidence)]
    if not _allows_cafe_results(parsed):
        items = [item for item in items if not _is_excluded_general_item(item)]
    result.items = items


def _reconcile_result_items(
    parsed: ParsedRequest,
    result: RecommendationResult,
    confirmed_blog_evidence: dict[str, str],
    candidates: list[SearchCandidate],
) -> int:
    _filter_result_items(parsed, result, confirmed_blog_evidence)
    if not candidates:
        return 0

    candidate_keys = {normalize_name(candidate.name) for candidate in candidates}
    ordered_items: list[RecommendationItem] = []
    seen_candidate_keys: set[str] = set()
    for item in result.items:
        candidate = _find_map_candidate(item.name, candidates)
        if candidate is None:
            continue
        candidate_key = normalize_name(candidate.name)
        if candidate_key not in candidate_keys or candidate_key in seen_candidate_keys:
            continue
        item.name = candidate.name
        if not item.category:
            item.category = candidate.category
        ordered_items.append(item)
        seen_candidate_keys.add(candidate_key)

    target_count = min(parsed.count, len(candidates))
    filled_count = 0
    if len(ordered_items) < target_count:
        for candidate in candidates:
            candidate_key = normalize_name(candidate.name)
            if candidate_key in seen_candidate_keys:
                continue
            fallback_item = _item_from_verified_candidate(
                candidate,
                confirmed_blog_evidence,
            )
            if fallback_item is None:
                continue
            if not _allows_cafe_results(parsed) and _is_excluded_general_item(fallback_item):
                continue
            ordered_items.append(fallback_item)
            seen_candidate_keys.add(candidate_key)
            filled_count += 1
            if len(ordered_items) >= target_count:
                break

    result.items = _rank_items_by_llm_fit(ordered_items)[:target_count]
    return filled_count


def _rank_items_by_llm_fit(items: list[RecommendationItem]) -> list[RecommendationItem]:
    if not any(_has_llm_fit_data(item) for item in items):
        return items
    return [
        item
        for _, item in sorted(
            enumerate(items),
            key=lambda pair: (-_llm_fit_score(pair[1]), pair[0]),
        )
    ]


def _has_llm_fit_data(item: RecommendationItem) -> bool:
    return bool(item.intent_fit or item.meal_fit or item.occasion_fit or item.risk_flags)


def _llm_fit_score(item: RecommendationItem) -> int:
    risk_penalty = min(12, len(set(item.risk_flags)) * 3)
    return item.intent_fit * 4 + item.occasion_fit * 2 + item.meal_fit - risk_penalty


def _item_from_verified_candidate(
    candidate: SearchCandidate,
    confirmed_blog_evidence: dict[str, str],
) -> RecommendationItem | None:
    links = _candidate_blog_links(candidate, confirmed_blog_evidence)
    if not links:
        return None
    evidence_text = confirmed_blog_evidence.get(links[0]["url"], "")
    return RecommendationItem(
        name=candidate.name,
        category=candidate.category,
        status_marker=_status_marker_from_evidence(evidence_text),
        reason=_fallback_reason_from_evidence(evidence_text),
        links=links,
        fit_tags=_fallback_fit_tags_from_evidence(evidence_text),
        tradeoff="영업시간은 제공된 근거만으로 확정하지 않았습니다.",
    )


def _candidate_blog_links(
    candidate: SearchCandidate,
    confirmed_blog_evidence: dict[str, str],
) -> list[dict[str, str]]:
    links: list[dict[str, str]] = []
    for url, evidence_text in confirmed_blog_evidence.items():
        if _blog_evidence_matches_item(candidate.name, evidence_text):
            links.append({"label": "네이버 블로그", "url": url})
        if len(links) >= 2:
            break
    return links


def _status_marker_from_evidence(evidence_text: str) -> str:
    if any(term in evidence_text for term in ("24시", "새벽", "심야", "야간", "늦게")):
        return "영업 가능성 높음"
    return "영업시간 미확인"


def _fallback_reason_from_evidence(evidence_text: str) -> str:
    if any(term in evidence_text for term in ("혼밥", "혼자")):
        return "네이버 블로그 후기에서 혼밥 관련 언급이 확인된 후보입니다."
    if any(term in evidence_text for term in ("데이트", "분위기")):
        return "네이버 블로그 후기에서 분위기 관련 언급이 확인된 후보입니다."
    if any(term in evidence_text for term in ("회식", "모임")):
        return "네이버 블로그 후기에서 모임 관련 언급이 확인된 후보입니다."
    return "네이버 블로그 후기에서 방문 경험이 확인된 후보입니다."


def _fallback_fit_tags_from_evidence(evidence_text: str) -> list[str]:
    tags: list[str] = []
    for label, terms in (
        ("혼밥", ("혼밥", "혼자")),
        ("분위기", ("데이트", "분위기", "조용")),
        ("모임", ("회식", "모임")),
        ("심야", ("24시", "새벽", "심야", "야간", "늦게")),
    ):
        if any(term in evidence_text for term in terms):
            tags.append(label)
    return tags[:4]


def _attach_map_candidates(
    items: list[RecommendationItem],
    candidates: list[SearchCandidate],
) -> None:
    if not items or not candidates:
        return
    for item in items:
        candidate = _find_map_candidate(item.name, candidates)
        if candidate is None:
            continue
        item.map_name = candidate.name
        item.map_address = candidate.address
        item.map_url = candidate.url


def _find_map_candidate(
    item_name: str,
    candidates: list[SearchCandidate],
) -> SearchCandidate | None:
    item_key = normalize_name(item_name)
    if not item_key:
        return None
    for candidate in candidates:
        if normalize_name(candidate.name) == item_key:
            return candidate
    for candidate in candidates:
        candidate_key = normalize_name(candidate.name)
        if _is_relaxed_map_candidate_match(item_key, candidate_key):
            return candidate
    return None


def _is_relaxed_map_candidate_match(item_key: str, candidate_key: str) -> bool:
    if len(item_key) < 3 or len(candidate_key) < 3:
        return False
    return item_key in candidate_key or candidate_key in item_key


def _allows_cafe_results(parsed: ParsedRequest) -> bool:
    text = " ".join([parsed.topic, parsed.meal_type, parsed.budget, parsed.occasion])
    return any(term in text for term in CAFE_INTENT_TERMS)


def _is_excluded_general_item(item: RecommendationItem) -> bool:
    text = f"{item.name} {item.category} {item.reason}".lower()
    if any(word.lower() in text for word in GENERAL_EXCLUDED_NAME_WORDS):
        return True
    return any(word in text for word in GENERAL_EXCLUDED_CATEGORY_WORDS)


def _has_confirmed_blog_link(
    item: RecommendationItem,
    confirmed_blog_evidence: dict[str, str],
) -> bool:
    for link in item.links:
        url = str(link.get("url") or "").strip()
        evidence_text = confirmed_blog_evidence.get(url)
        if evidence_text and _blog_evidence_matches_item(item.name, evidence_text):
            return True
    return False


def _confirmed_blog_evidence(
    context: str,
    allowed_domains: tuple[str, ...] = ("blog.naver.com",),
) -> dict[str, str]:
    evidence: dict[str, str] = {}
    for line in context.splitlines():
        for match in re.finditer(r"https?://[^\s]+", line):
            url = match.group(0).rstrip(".,)]}")
            host = urlparse(url).netloc.lower()
            if any(host == domain or host.endswith("." + domain) for domain in allowed_domains):
                evidence[url] = line
    return evidence


def _blog_evidence_matches_item(item_name: str, evidence_text: str) -> bool:
    name = normalize_name(item_name)
    evidence = normalize_name(evidence_text)
    if not name or not evidence:
        return False
    if name in evidence:
        return True
    tokens = _significant_name_tokens(item_name)
    return bool(tokens) and any(token in evidence for token in tokens)


def _significant_name_tokens(item_name: str) -> list[str]:
    tokens: list[str] = []
    for token in re.split(r"[\s,/()]+", item_name):
        normalized = normalize_name(token)
        if len(normalized) >= 2 and normalized not in {"본점", "점"}:
            tokens.append(normalized)
    return tokens


def _naver_evidence_unavailable_response(search_context: SearchContext) -> str:
    if not search_context.configured:
        return "Naver 근거를 가져오지 못했어요. Naver API 설정을 확인한 뒤 다시 시도해주세요."
    if search_context.quota_blocked:
        return "Naver 근거를 가져오지 못했어요. 오늘 Naver API 한도 상태를 확인한 뒤 다시 시도해주세요."
    return "Naver 근거를 충분히 가져오지 못했어요. 잠시 후 다시 시도해주세요."


def _format_log_fields(fields: dict[str, object]) -> str:
    parts: list[str] = []
    for key, value in fields.items():
        if value is None:
            continue
        if isinstance(value, bool):
            text = "true" if value else "false"
        else:
            text = str(value)
        text = " ".join(text.split())
        parts.append(f"{key}={text}")
    return " ".join(parts)


def _mask_identifier(value: str) -> str:
    text = str(value)
    if len(text) <= 4:
        return "***"
    return "***" + text[-4:]
