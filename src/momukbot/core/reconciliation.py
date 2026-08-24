from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlparse

from momukbot.core.formatter import kakao_place_url, normalize_name
from momukbot.core.models import (
    DropReason,
    EvidenceBundle,
    ParsedRequest,
    RecommendationItem,
    RecommendationResult,
    SearchCandidate,
    VerifiedCandidate,
)
from momukbot.core.policy import (
    exact_food_allowed_terms,
    intent_allows_cafe,
    intent_allows_fast_food,
    is_excluded_general_text,
    should_apply_diversity_rerank,
)


DIVERSITY_REPEAT_PENALTY = 5
LOW_CONFIDENCE_THRESHOLD = 3
WEAK_FIT_RISK_FLAGS = ("weak_fit", "menu_unclear", "occasion_mismatch")


@dataclass(frozen=True)
class ReconcileStats:
    initial_item_count: int
    item_count: int
    candidate_count: int
    accepted_evaluation_count: int
    rejected_evaluation_count: int
    filled_count: int
    confirmed_blog_url_count: int
    confirmed_candidate_blog_link_count: int
    exact_food_filtered_count: int = 0
    weak_fit_filtered_count: int = 0
    drop_reasons: dict[str, int] | None = None

    @property
    def changed(self) -> bool:
        return (
            self.item_count != self.initial_item_count
            or self.rejected_evaluation_count > 0
            or self.filled_count > 0
            or self.exact_food_filtered_count > 0
            or self.weak_fit_filtered_count > 0
            or bool(self.drop_reasons)
        )


def reconcile_result_items(
    parsed: ParsedRequest,
    result: RecommendationResult,
    confirmed_blog_evidence: dict[str, str],
    candidates: list[SearchCandidate],
    verified_candidates: list[VerifiedCandidate] | tuple[VerifiedCandidate, ...] = (),
) -> ReconcileStats:
    initial_item_count = len(result.items)
    drop_reasons: dict[str, int] = {}
    candidate_evidence = _candidate_evidence_by_key(verified_candidates)
    if verified_candidates and not candidates:
        candidates = [verified.candidate for verified in verified_candidates]
    if candidates:
        original_candidate_count = len(candidates)
        candidates = [candidate for candidate in candidates if kakao_place_url(candidate.url)]
        _add_drop_reason(
            drop_reasons,
            DropReason.NO_KAKAO_URL,
            original_candidate_count - len(candidates),
        )
        if not candidates:
            result.items = []
            return ReconcileStats(
                initial_item_count=initial_item_count,
                item_count=0,
                candidate_count=0,
                accepted_evaluation_count=0,
                rejected_evaluation_count=initial_item_count,
                filled_count=0,
                confirmed_blog_url_count=len(confirmed_blog_evidence),
                confirmed_candidate_blog_link_count=0,
                drop_reasons=drop_reasons,
            )
    if not candidates:
        _filter_result_items(parsed, result, candidate_evidence)
        item_count = len(result.items)
        return ReconcileStats(
            initial_item_count=initial_item_count,
            item_count=item_count,
            candidate_count=0,
            accepted_evaluation_count=item_count,
            rejected_evaluation_count=max(0, initial_item_count - item_count),
            filled_count=0,
            confirmed_blog_url_count=len(confirmed_blog_evidence),
            confirmed_candidate_blog_link_count=confirmed_candidate_blog_link_count(result.items),
            drop_reasons=drop_reasons,
        )

    candidate_keys = {normalize_name(candidate.name) for candidate in candidates}
    ordered_items: list[RecommendationItem] = []
    seen_candidate_keys: set[str] = set()
    for item in result.items:
        candidate = _find_map_candidate(item.name, candidates)
        if candidate is None:
            _add_drop_reason(drop_reasons, DropReason.MISSING_CANDIDATE)
            continue
        candidate_key = normalize_name(candidate.name)
        if candidate_key not in candidate_keys:
            _add_drop_reason(drop_reasons, DropReason.MISSING_CANDIDATE)
            continue
        if candidate_key in seen_candidate_keys:
            _add_drop_reason(drop_reasons, DropReason.DUPLICATE)
            continue
        links = _candidate_blog_links(candidate, candidate_evidence)
        if not links:
            _add_drop_reason(drop_reasons, DropReason.MISSING_EVIDENCE)
            continue
        item.name = candidate.name
        if not item.category:
            item.category = candidate.category
        item.links = links
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
                candidate_evidence,
            )
            if fallback_item is None:
                _add_drop_reason(drop_reasons, DropReason.MISSING_EVIDENCE)
                continue
            if not _allows_cafe_results(parsed) and _is_excluded_general_item(
                fallback_item,
                allow_fast_food=_allows_fast_food_results(parsed),
            ):
                _add_drop_reason(drop_reasons, DropReason.CAFE_FAST_FOOD_EXCLUDED)
                continue
            ordered_items.append(fallback_item)
            seen_candidate_keys.add(candidate_key)
            filled_count += 1
            if len(ordered_items) >= target_count:
                break

    ordered_items, exact_food_filtered_count, exact_food_filtered_names = _filter_exact_food_items(parsed, ordered_items)
    _add_drop_reason(drop_reasons, DropReason.EXACT_FOOD_MISMATCH, exact_food_filtered_count)
    _drop_summary_if_mentions_removed_candidate(result, exact_food_filtered_names)
    ordered_items, weak_fit_filtered_count, weak_fit_filtered_names = _filter_weak_fit_items(ordered_items)
    _add_drop_reason(drop_reasons, DropReason.WEAK_FIT, weak_fit_filtered_count)
    _drop_summary_if_mentions_removed_candidate(result, weak_fit_filtered_names)
    result.items = _rank_items_by_llm_fit(ordered_items, parsed)[:target_count]
    item_count = len(result.items)
    accepted_evaluation_count = len(seen_candidate_keys) - filled_count
    return ReconcileStats(
        initial_item_count=initial_item_count,
        item_count=item_count,
        candidate_count=len(candidates),
        accepted_evaluation_count=max(0, accepted_evaluation_count),
        rejected_evaluation_count=max(0, initial_item_count - accepted_evaluation_count),
        filled_count=filled_count,
        confirmed_blog_url_count=len(confirmed_blog_evidence),
        confirmed_candidate_blog_link_count=confirmed_candidate_blog_link_count(result.items),
        exact_food_filtered_count=exact_food_filtered_count,
        weak_fit_filtered_count=weak_fit_filtered_count,
        drop_reasons=drop_reasons,
    )


def attach_map_candidates(
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


def confirmed_blog_evidence(
    verified_candidates: list[VerifiedCandidate] | tuple[VerifiedCandidate, ...],
    allowed_domains: tuple[str, ...] = ("blog.naver.com",),
) -> dict[str, str]:
    evidence: dict[str, str] = {}
    for verified in verified_candidates:
        for item in verified.evidence:
            host = urlparse(item.url).netloc.lower()
            if any(host == domain or host.endswith("." + domain) for domain in allowed_domains):
                evidence[item.url] = item.text
    return evidence


def format_drop_reasons(drop_reasons: dict[str, int] | None) -> str:
    if not drop_reasons:
        return ""
    return ",".join(f"{reason}:{count}" for reason, count in sorted(drop_reasons.items()))


def multi_blog_candidate_count(items: list[RecommendationItem]) -> int:
    return sum(1 for item in items if sum(1 for link in item.links if _is_allowed_blog_link(link)) >= 2)


def diversity_group_count(items: list[RecommendationItem]) -> int:
    groups = {_diversity_key(item) for item in items}
    groups.discard("")
    return len(groups)


def average_confidence(items: list[RecommendationItem]) -> str:
    values: list[int] = []
    for item in items:
        if item.confidence is not None:
            values.append(item.confidence)
    if not values:
        return "0"
    return f"{sum(values) / len(values):.2f}"


def confirmed_candidate_blog_link_count(items: list[RecommendationItem]) -> int:
    return sum(1 for item in items if any(_is_allowed_blog_link(link) for link in item.links))


def _filter_result_items(
    parsed: ParsedRequest,
    result: RecommendationResult,
    candidate_evidence: dict[str, tuple[EvidenceBundle, ...]],
) -> None:
    items = [item for item in result.items if _has_confirmed_blog_link(item, candidate_evidence)]
    if not _allows_cafe_results(parsed):
        allow_fast_food = _allows_fast_food_results(parsed)
        items = [item for item in items if not _is_excluded_general_item(item, allow_fast_food=allow_fast_food)]
    result.items = items


def _candidate_evidence_by_key(
    verified_candidates: list[VerifiedCandidate] | tuple[VerifiedCandidate, ...],
) -> dict[str, tuple[EvidenceBundle, ...]]:
    evidence_by_key: dict[str, tuple[EvidenceBundle, ...]] = {}
    for verified in verified_candidates:
        key = normalize_name(verified.candidate.name)
        if not key:
            continue
        evidence_by_key[key] = tuple(
            evidence for evidence in verified.evidence if evidence.source == "naver_blog"
        )
    return evidence_by_key


def _add_drop_reason(drop_reasons: dict[str, int], reason: str, count: int = 1) -> None:
    if count <= 0:
        return
    drop_reasons[reason] = drop_reasons.get(reason, 0) + count


def _is_allowed_blog_link(link: dict[str, str]) -> bool:
    url = str(link.get("url") or "").strip()
    return _is_allowed_blog_url(url)


def _is_allowed_blog_url(url: str) -> bool:
    if not url:
        return False
    host = urlparse(url).netloc.lower()
    return host == "blog.naver.com" or host.endswith(".blog.naver.com")


def _rank_items_by_llm_fit(
    items: list[RecommendationItem],
    parsed: ParsedRequest | None = None,
) -> list[RecommendationItem]:
    if not any(_has_llm_fit_data(item) for item in items):
        return items
    ranked = [
        item
        for _, item in sorted(
            enumerate(items),
            key=lambda pair: (-_llm_fit_score(pair[1]), pair[0]),
        )
    ]
    if parsed is None or not _should_apply_diversity_rerank(parsed):
        return ranked
    return _diversity_aware_rerank(ranked)


def _filter_exact_food_items(
    parsed: ParsedRequest,
    items: list[RecommendationItem],
) -> tuple[list[RecommendationItem], int, list[str]]:
    allowed_terms = exact_food_allowed_terms(parsed.topic)
    if not allowed_terms:
        return items, 0, []
    filtered = [item for item in items if _matches_exact_food_family(item, allowed_terms)]
    removed_names = [item.name for item in items if item not in filtered]
    return filtered, len(removed_names), removed_names


def _drop_summary_if_mentions_removed_candidate(
    result: RecommendationResult,
    removed_names: list[str],
) -> None:
    if not result.top_summary or not removed_names:
        return
    if any(name and name in result.top_summary for name in removed_names):
        result.top_summary = ""


def _filter_weak_fit_items(items: list[RecommendationItem]) -> tuple[list[RecommendationItem], int, list[str]]:
    filtered: list[RecommendationItem] = []
    removed_names: list[str] = []
    for item in items:
        if _is_weak_fit_item(item):
            removed_names.append(item.name)
        else:
            filtered.append(item)
    return filtered, len(removed_names), removed_names


def _is_weak_fit_item(item: RecommendationItem) -> bool:
    risk_flags = {flag.strip() for flag in item.risk_flags}
    if "weak_fit" in risk_flags:
        return True
    if item.confidence is not None and item.confidence < LOW_CONFIDENCE_THRESHOLD:
        return True
    return item.confidence is not None and item.confidence <= LOW_CONFIDENCE_THRESHOLD and any(
        flag in risk_flags for flag in WEAK_FIT_RISK_FLAGS
    )


def _matches_exact_food_family(item: RecommendationItem, allowed_terms: tuple[str, ...]) -> bool:
    text = " ".join(
        [
            item.name,
            item.category,
            item.menu_family,
            item.diversity_group,
            item.best_for,
            *item.fit_tags,
        ]
    )
    return any(term in text for term in allowed_terms)


def _has_llm_fit_data(item: RecommendationItem) -> bool:
    return item.confidence is not None or bool(
        item.intent_fit
        or item.meal_fit
        or item.occasion_fit
        or item.evidence_quality
        or item.risk_flags
        or item.menu_family
        or item.diversity_group
    )


def _llm_fit_score(item: RecommendationItem) -> int:
    risk_penalty = min(12, len(set(item.risk_flags)) * 3)
    return (
        item.intent_fit * 4
        + item.occasion_fit * 2
        + item.meal_fit
        + item.evidence_quality
        + (item.confidence or 0)
        - risk_penalty
    )


def _should_apply_diversity_rerank(parsed: ParsedRequest) -> bool:
    return should_apply_diversity_rerank(parsed.topic)


def _diversity_aware_rerank(items: list[RecommendationItem]) -> list[RecommendationItem]:
    remaining = list(enumerate(items))
    selected: list[RecommendationItem] = []
    group_counts: dict[str, int] = {}
    while remaining:
        best_remaining_index = max(
            range(len(remaining)),
            key=lambda idx: _diversity_rank_key(remaining[idx], group_counts),
        )
        _, item = remaining.pop(best_remaining_index)
        selected.append(item)
        group = _diversity_key(item)
        if group:
            group_counts[group] = group_counts.get(group, 0) + 1
    return selected


def _diversity_rank_key(
    pair: tuple[int, RecommendationItem],
    group_counts: dict[str, int],
) -> tuple[int, int, int]:
    order, item = pair
    group = _diversity_key(item)
    repeat_count = group_counts.get(group, 0) if group else 0
    adjusted_score = _llm_fit_score(item) - repeat_count * DIVERSITY_REPEAT_PENALTY
    return adjusted_score, -repeat_count, -order


def _diversity_key(item: RecommendationItem) -> str:
    for value in (item.diversity_group, item.menu_family, item.category):
        key = normalize_name(value)
        if key:
            return key
    return ""


def _item_from_verified_candidate(
    candidate: SearchCandidate,
    candidate_evidence: dict[str, tuple[EvidenceBundle, ...]],
) -> RecommendationItem | None:
    links = _candidate_blog_links(candidate, candidate_evidence)
    if not links:
        return None
    evidence_items = candidate_evidence.get(normalize_name(candidate.name), ())
    evidence_text = evidence_items[0].text if evidence_items else ""
    return RecommendationItem(
        name=candidate.name,
        category=candidate.category,
        status_marker=_status_marker_from_evidence(evidence_text),
        reason=_fallback_reason_from_evidence(evidence_text),
        links=links,
        fit_tags=_fallback_fit_tags_from_evidence(evidence_text),
        tradeoff=_fallback_tradeoff_from_evidence(evidence_text),
    )


def _candidate_blog_links(
    candidate: SearchCandidate,
    candidate_evidence: dict[str, tuple[EvidenceBundle, ...]],
) -> list[dict[str, str]]:
    links: list[dict[str, str]] = []
    for evidence in candidate_evidence.get(normalize_name(candidate.name), ()):
        if _is_allowed_blog_url(evidence.url):
            links.append({"label": "네이버 블로그", "url": evidence.url})
        if len(links) >= 2:
            break
    return links


def _status_marker_from_evidence(evidence_text: str) -> str:
    if any(term in evidence_text for term in ("24시", "새벽", "심야", "야간", "늦게")):
        return "영업 가능성 높음"
    return "영업시간 미확인"


def _fallback_reason_from_evidence(evidence_text: str) -> str:
    if _has_unlimited_refill_signal(evidence_text):
        return "무제한으로 먹기 좋은 구성이 언급되어 든든하게 먹고 싶을 때 검토할 만합니다."
    if any(term in evidence_text for term in ("혼밥", "혼자")):
        return "혼자 먹기 편하다는 언급이 있어 혼밥 요청에 잘 맞습니다."
    if any(term in evidence_text for term in ("데이트", "분위기")):
        return "분위기 관련 언급이 있어 데이트나 편한 식사에 맞습니다."
    if any(term in evidence_text for term in ("회식", "모임")):
        return "모임 관련 언급이 있어 여럿이 가는 식사에 맞습니다."
    return "방문 경험이 있는 식사 후보라 무난하게 검토할 만합니다."


def _fallback_tradeoff_from_evidence(evidence_text: str) -> str:
    if _has_unlimited_refill_signal(evidence_text):
        return "무한리필 특성상 혼밥보다는 여럿이 가기 편할 수 있습니다. 가격이나 시간제한은 방문 전 확인이 필요합니다."
    return "영업시간은 제공된 근거만으로 확정하지 않았습니다."


def _fallback_fit_tags_from_evidence(evidence_text: str) -> list[str]:
    tags: list[str] = []
    for label, terms in (
        ("무한리필", ("무한리필", "무제한", "뷔페", "부페", "샐러드바", "리필")),
        ("샤브샤브", ("샤브샤브", "월남쌈", "편백찜")),
        ("가성비", ("가성비", "저렴", "가격", "1인 가격")),
        ("혼밥", ("혼밥", "혼자")),
        ("분위기", ("데이트", "분위기", "조용")),
        ("모임", ("회식", "모임")),
        ("심야", ("24시", "새벽", "심야", "야간", "늦게")),
    ):
        if any(term in evidence_text for term in terms):
            tags.append(label)
    return tags[:4]


def _has_unlimited_refill_signal(evidence_text: str) -> bool:
    return any(
        term in evidence_text
        for term in ("무한리필", "무제한", "뷔페", "부페", "샐러드바", "리필", "월남쌈", "샤브샤브", "편백찜")
    )


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
    return intent_allows_cafe(parsed.topic, parsed.meal_type, parsed.budget, parsed.occasion)


def _allows_fast_food_results(parsed: ParsedRequest) -> bool:
    return intent_allows_fast_food(parsed.topic, parsed.meal_type, parsed.budget, parsed.occasion)


def _is_excluded_general_item(item: RecommendationItem, allow_fast_food: bool = False) -> bool:
    text = f"{item.name} {item.category} {item.reason}".lower()
    return is_excluded_general_text(text, allow_fast_food=allow_fast_food, case_sensitive=False)


def _has_confirmed_blog_link(
    item: RecommendationItem,
    candidate_evidence: dict[str, tuple[EvidenceBundle, ...]],
) -> bool:
    evidence_urls = {
        evidence.url
        for evidence in candidate_evidence.get(normalize_name(item.name), ())
        if _is_allowed_blog_url(evidence.url)
    }
    for link in item.links:
        url = str(link.get("url") or "").strip()
        if url in evidence_urls:
            return True
    return False
