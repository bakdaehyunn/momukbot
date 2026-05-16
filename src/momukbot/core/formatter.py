from __future__ import annotations

from urllib.parse import quote, urlparse

from .models import RecommendationItem


def normalize_name(name: str) -> str:
    return "".join(ch.lower() for ch in name if ch.isalnum())


def naver_map_search_url(place_name: str, area: str = "") -> str:
    query = place_name.strip()
    clean_area = area.strip()
    if clean_area and normalize_name(clean_area) not in normalize_name(query):
        query = f"{clean_area} {query}"
    return "https://map.naver.com/p/search/" + quote(query, safe="")


def filter_preferred_links(
    links: list[dict[str, str]],
    allowed_domains: tuple[str, ...] = ("blog.naver.com",),
) -> list[dict[str, str]]:
    preferred: list[dict[str, str]] = []
    fallback: list[dict[str, str]] = []
    for link in links:
        url = str(link.get("url") or "").strip()
        if not url.startswith(("http://", "https://")):
            continue
        host = urlparse(url).netloc.lower()
        if host == "tistory.com" or host.endswith(".tistory.com"):
            continue
        item = {"label": str(link.get("label") or "링크").strip(), "url": url}
        if any(host == domain or host.endswith("." + domain) for domain in allowed_domains):
            if item["label"] not in {"블로그", "리뷰"}:
                item["label"] = "블로그"
            preferred.append(item)
        else:
            fallback.append(item)
    return (preferred + fallback)[:2]


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
) -> str:
    if not items:
        return "이번 요청에서는 추천할 후보를 찾지 못했습니다."
    lines: list[str] = []
    title = recommendation_title(keyword, area, len(items))
    lines.extend([title, ""])
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


def format_item_lines(idx: int, item: RecommendationItem, area: str = "") -> list[str]:
    lines = [f"{idx}. {item.name}"]
    if item.status_marker:
        lines.append(f"   상태: {item.status_marker}")
    if item.reason:
        lines.append(f"   이유: {item.reason}")
    used: set[str] = set()
    for link in item.links[:2]:
        url = link.get("url") or ""
        if url and url not in used:
            lines.append(f"   근거: {url}")
            used.add(url)
    map_url = naver_map_search_url(item.name, area=area)
    if map_url not in used:
        lines.append(f"   지도: {map_url}")
    return lines
