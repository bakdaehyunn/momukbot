from __future__ import annotations

from urllib.parse import quote, urlparse

from .models import RecommendationItem

INTERNAL_REASONING_TERMS = (
    "네이버",
    "블로그",
    "로컬",
    "근거",
    "검증",
    "후기 우선",
    "최근 방문 후기",
    "장소 확인",
    "장소 일치",
)
USER_FACING_CRITERIA_TERMS = (
    "혼밥",
    "혼술",
    "데이트",
    "회식",
    "조용",
    "늦",
    "심야",
    "야식",
    "가성비",
    "저렴",
    "고급",
    "매운",
    "국물",
    "해장",
    "고기",
    "초밥",
    "일식",
    "중식",
    "한식",
    "분식",
    "칼국수",
    "솥밥",
    "혼자",
    "부담",
)

def normalize_name(name: str) -> str:
    return "".join(ch.lower() for ch in name if ch.isalnum())


def naver_map_search_url(place_name: str, area: str = "") -> str:
    query = place_name.strip()
    clean_area = area.strip()
    if clean_area and normalize_name(clean_area) not in normalize_name(query):
        query = f"{clean_area} {query}"
    return "https://map.naver.com/p/search/" + quote(query, safe="")


def preferred_naver_map_url(candidate_url: str, place_name: str, area: str = "") -> str:
    clean_url = candidate_url.strip()
    if _is_naver_map_url(clean_url):
        return clean_url
    return naver_map_search_url(place_name, area=area)


def filter_preferred_links(
    links: list[dict[str, str]],
    allowed_domains: tuple[str, ...] = ("blog.naver.com",),
) -> list[dict[str, str]]:
    preferred: list[dict[str, str]] = []
    for link in links:
        url = str(link.get("url") or "").strip()
        if not url.startswith(("http://", "https://")):
            continue
        host = urlparse(url).netloc.lower()
        if host == "tistory.com" or host.endswith(".tistory.com"):
            continue
        item = {"label": str(link.get("label") or "링크").strip(), "url": url}
        if _is_allowed_blog_url(url, allowed_domains):
            item["label"] = "네이버 블로그"
            preferred.append(item)
    return preferred[:2]


def normalize_category(item: RecommendationItem) -> str:
    raw = (item.category or "").strip()
    text = f"{raw} {item.name} {item.reason}".lower()
    if "감자탕" in text or "뼈해장" in text:
        return "감자탕/뼈해장국"
    if "국밥" in text or "순대국" in text or "돼지국" in text:
        return "국밥"
    if "해장국" in text or "콩나물" in text:
        return "해장국"
    if "술집" in text or "펍" in text or "와인바" in text or raw == "바":
        return "술집/바"
    if "카페" in text:
        return "카페"
    if "일식" in text or "초밥" in text or "라멘" in text:
        return "일식"
    if "중식" in text or "마라" in text or "짬뽕" in text:
        return "중식"
    if raw:
        return raw
    return "기타"


def format_recommendation_message(
    keyword: str,
    items: list[RecommendationItem],
    area: str = "",
    decision_criteria: list[str] | None = None,
    top_summary: str = "",
) -> str:
    if not items:
        return "이번 요청에서는 추천할 후보를 찾지 못했습니다."
    lines: list[str] = []
    title = recommendation_title(keyword, area, len(items))
    lines.extend([title, ""])
    clean_criteria = user_facing_decision_criteria(decision_criteria or [])
    clean_top_summary = user_facing_summary(top_summary)
    if clean_criteria:
        lines.extend([decision_criteria_summary(clean_criteria), ""])
    if clean_top_summary:
        lines.extend([clean_top_summary, ""])
    if len(items) >= 3:
        lines.extend([top_picks_summary(items), ""])
    if clean_criteria or clean_top_summary:
        lines.append("[추천 순서]")
        for idx, item in enumerate(items, start=1):
            lines.extend(format_item_lines(idx, item, area=area))
            if idx < len(items):
                lines.append("")
        return "\n".join(lines).rstrip()

    grouped: dict[str, list[RecommendationItem]] = {}
    order: list[str] = []
    for item in items:
        category = normalize_category(item)
        if category not in grouped:
            grouped[category] = []
            order.append(category)
        grouped[category].append(item)

    idx = 1
    total = len(items)
    for category in order:
        lines.append(f"[{category}]")
        for item in grouped[category]:
            lines.extend(format_item_lines(idx, item, area=area))
            idx += 1
            if idx <= total:
                lines.append("")
    return "\n".join(lines).rstrip()


def recommendation_title(keyword: str, area: str, total: int) -> str:
    clean_keyword = keyword.strip()
    clean_area = area.strip()
    if clean_keyword:
        basis = clean_keyword
        if clean_area and normalize_name(clean_area) not in normalize_name(clean_keyword):
            basis = f"{clean_area} {clean_keyword}"
    elif clean_area:
        basis = f"{clean_area} 맛집"
    else:
        basis = "맛집"
    return f"{basis} 추천 {total}곳"


def top_picks_summary(items: list[RecommendationItem]) -> str:
    lines = ["먼저 볼 3곳:"]
    for item in items[:3]:
        if not item.name:
            continue
        hint = top_pick_hint(item)
        if hint:
            lines.append(f"- {item.name}: {hint}")
        else:
            lines.append(f"- {item.name}")
    return "\n".join(lines)


def top_pick_hint(item: RecommendationItem) -> str:
    if item.fit_tags:
        return " · ".join(item.fit_tags[:2])
    category = normalize_category(item)
    if category != "기타":
        return category
    return ""


def decision_criteria_summary(decision_criteria: list[str]) -> str:
    criteria = ", ".join(item.strip() for item in decision_criteria if item.strip())
    return f"이번 요청 기준: {criteria}"


def user_facing_decision_criteria(decision_criteria: list[str]) -> list[str]:
    clean: list[str] = []
    for item in decision_criteria:
        text = item.strip()
        if not text or _contains_internal_reasoning(text):
            continue
        if not _is_user_facing_criterion(text):
            continue
        clean.append(text)
    return clean[:4]


def user_facing_summary(summary: str) -> str:
    text = summary.strip()
    if not text or _contains_internal_reasoning(text):
        return ""
    return text


def format_item_lines(idx: int, item: RecommendationItem, area: str = "") -> list[str]:
    lines = [f"{idx}. {item.name}"]
    if item.status_marker and item.status_marker != "영업시간 미확인":
        lines.append(f"   상태: {item.status_marker}")
    if item.fit_tags:
        lines.append(f"   포인트: {' · '.join(item.fit_tags[:4])}")
    if item.reason:
        lines.append(f"   이유: {item.reason}")
    if item.tradeoff:
        lines.append(f"   참고: {item.tradeoff}")
    used: set[str] = set()
    for link in item.links[:2]:
        url = link.get("url") or ""
        if url and url not in used and _is_allowed_blog_url(url):
            lines.append(f"   블로그: {url}")
            used.add(url)
    map_name = (item.map_name or item.name).strip()
    map_url = preferred_naver_map_url(item.map_url, map_name or item.name, area=area)
    if map_url not in used:
        if item.map_address.strip():
            lines.append(f"   주소: {item.map_address.strip()}")
        lines.append(f"   지도: {map_url}")
    return lines


def _is_allowed_blog_url(
    url: str,
    allowed_domains: tuple[str, ...] = ("blog.naver.com",),
) -> bool:
    host = urlparse(url).netloc.lower()
    return any(host == domain or host.endswith("." + domain) for domain in allowed_domains)


def _contains_internal_reasoning(text: str) -> bool:
    return any(term in text for term in INTERNAL_REASONING_TERMS)


def _is_user_facing_criterion(text: str) -> bool:
    return any(term in text for term in USER_FACING_CRITERIA_TERMS)


def _is_naver_map_url(url: str) -> bool:
    if not url:
        return False
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        return False
    host = parsed.netloc.lower()
    if host == "app.map.naver.com" or host.endswith(".app.map.naver.com"):
        return False
    return (
        host == "naver.me"
        or host.endswith(".naver.me")
        or host == "map.naver.com"
        or host.endswith(".map.naver.com")
    )
