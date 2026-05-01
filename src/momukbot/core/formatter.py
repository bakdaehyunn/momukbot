from __future__ import annotations

from urllib.parse import quote, urlparse

from .models import RecommendationItem


def normalize_name(name: str) -> str:
    return "".join(ch.lower() for ch in name if ch.isalnum())


def naver_map_search_url(place_name: str) -> str:
    return "https://map.naver.com/p/search/" + quote(place_name.strip(), safe="")


def filter_preferred_links(
    links: list[dict[str, str]],
    allowed_domains: tuple[str, ...] = ("blog.naver.com", "tistory.com"),
) -> list[dict[str, str]]:
    preferred: list[dict[str, str]] = []
    fallback: list[dict[str, str]] = []
    for link in links:
        url = str(link.get("url") or "").strip()
        if not url.startswith(("http://", "https://")):
            continue
        host = urlparse(url).netloc.lower()
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


def format_recommendation_message(keyword: str, items: list[RecommendationItem]) -> str:
    if not items:
        return "이번 요청에서는 추천할 후보를 찾지 못했습니다."
    lines: list[str] = []
    if keyword:
        lines.extend([f"검색 키워드: {keyword}", ""])
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
            lines.extend(format_item_lines(idx, item))
            idx += 1
            if idx <= total:
                lines.append("")
    return "\n".join(lines).rstrip()


def format_item_lines(idx: int, item: RecommendationItem) -> list[str]:
    lines = [f"{idx}. {item.name} - {item.status_marker}"]
    if item.reason:
        lines.append(f"   이유: {item.reason}")
    used: set[str] = set()
    for link in item.links[:2]:
        label = link.get("label") or "링크"
        url = link.get("url") or ""
        if url and url not in used:
            lines.append(f"   {label}: {url}")
            used.add(url)
    map_url = naver_map_search_url(item.name)
    if map_url not in used:
        lines.append(f"   네이버지도: {map_url}")
    return lines
