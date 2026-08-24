from __future__ import annotations

from momukbot.core.formatter import filter_preferred_links, normalize_name
from momukbot.core.json_utils import extract_json_object
from momukbot.core.models import RecommendationItem, RecommendationResult


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
    raw_items = data.get("evaluations")
    if not isinstance(raw_items, list):
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
                    evidence_quality=_bounded_int(raw_item.get("evidence_quality"), minimum=0, maximum=5),
                    risk_flags=_string_list(raw_item.get("risk_flags"), limit=4),
                    menu_family=str(raw_item.get("menu_family") or "").strip(),
                    best_for=str(raw_item.get("best_for") or "").strip(),
                    diversity_group=str(raw_item.get("diversity_group") or "").strip(),
                    confidence=_optional_bounded_int(raw_item, "confidence", minimum=0, maximum=5),
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


def _optional_bounded_int(data: dict[str, object], key: str, minimum: int, maximum: int) -> int | None:
    if key not in data or data[key] is None:
        return None
    return _bounded_int(data[key], minimum, maximum)
