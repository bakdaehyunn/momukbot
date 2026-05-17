from __future__ import annotations

import re

from .models import ParsedRequest


FOOD_HINTS = (
    "맛집",
    "밥",
    "밥집",
    "식당",
    "아침",
    "점심",
    "저녁",
    "야식",
    "술집",
    "혼술",
    "혼술바",
    "펍",
    "바",
    "와인바",
    "쭈꾸미",
    "해장",
    "고기",
    "초밥",
    "국밥",
    "돼지국밥",
    "감자탕",
    "뼈해장국",
    "해장국",
    "혼밥",
    "회식",
    "데이트",
    "커피집",
    "베이커리",
    "디저트",
    "카페",
    "커피",
    "빵",
    "뭐 먹",
    "뭐먹",
)
ACTION_HINTS = ("추천", "찾아", "알려", "보여", "가기 좋은", "갈만한", "뭐", "어디", "위주")
COMMAND_PREFIXES = ("/맛집", "/momuk", "/뭐먹")
WORK_INTENT_HINTS = (
    "데이터",
    "보고서",
    "회의록",
    "회의자료",
    "자료",
    "매출",
    "엑셀",
    "스프레드시트",
    "문서",
    "분석",
    "정리",
    "작성",
    "만들어",
    "준비",
)
AREA_PARTICLES = ("에서", "근처", "기준", "주변")
AREA_PREFIX_TRASH = ("오늘", "내일", "지금", "이번주", "이번 주", "주말")
LOCATION_SUFFIXES = (
    "센트럴파크",
    "해수욕장",
    "한옥마을",
    "터미널",
    "대학가",
    "공항",
    "시장",
    "입구",
    "거리",
    "대로",
    "번가",
    "역",
    "동",
    "로",
    "길",
    "구",
    "시",
    "군",
    "읍",
    "면",
    "리",
    "도",
)
TOPIC_HINTS = (
    "쭈꾸미",
    "맛집",
    "돼지국밥",
    "감자탕",
    "뼈해장국",
    "해장국",
    "국밥",
    "해장",
    "펍",
    "와인바",
    "야식",
    "술집",
    "초밥",
    "고기",
    "커피집",
    "베이커리",
    "디저트",
    "카페",
    "커피",
    "빵",
)
AREA_STOP_MARKERS = tuple(
    dict.fromkeys(
        sorted(
            FOOD_HINTS + ACTION_HINTS + TOPIC_HINTS + ("술자리",),
            key=len,
            reverse=True,
        )
    )
)
AREA_PARTICLE_RE = "|".join(re.escape(item) for item in AREA_PARTICLES)
AREA_STOP_RE = "|".join(re.escape(item) for item in AREA_STOP_MARKERS)


def _clean_area(area: str) -> str:
    cleaned = re.sub(r"\s+", " ", area).strip(" ,.!?`'\"")
    for prefix in AREA_PREFIX_TRASH:
        if cleaned.startswith(prefix + " "):
            cleaned = cleaned[len(prefix) :].strip()
            break
    return cleaned


def _has_location_shape(area: str) -> bool:
    tokens = area.split()
    if not tokens:
        return False
    return any(token.endswith(LOCATION_SUFFIXES) for token in tokens)


def _extract_area(raw: str) -> str:
    particle_match = re.search(rf"^(.+?)\s*(?:{AREA_PARTICLE_RE})", raw)
    if particle_match:
        area = _clean_area(particle_match.group(1))
        if area:
            return area

    if raw.startswith(("/맛집", "/momuk", "/뭐먹")):
        parts = raw.split(maxsplit=2)
        return _clean_area(parts[1]) if len(parts) >= 2 else ""

    marker_match = re.search(rf"^(.+?)\s+(?:{AREA_STOP_RE})(?:\s|$)", raw)
    if marker_match:
        area = _clean_area(marker_match.group(1))
        if area and len(area.split()) <= 4:
            return area

    location_match = re.search(
        rf"^([가-힣A-Za-z0-9 ]*?[가-힣A-Za-z0-9]+(?:{'|'.join(map(re.escape, LOCATION_SUFFIXES))}))\s+.+(?:추천|찾아|알려|보여)",
        raw,
    )
    if location_match:
        area = _clean_area(location_match.group(1))
        if area and _has_location_shape(area):
            return area

    return ""


def looks_like_restaurant_message(text: str) -> bool:
    raw = text.strip()
    if not raw:
        return False
    if raw.startswith(COMMAND_PREFIXES):
        return True
    if any(key in raw for key in WORK_INTENT_HINTS):
        return False
    has_food = any(key in raw for key in FOOD_HINTS)
    has_action = any(key in raw for key in ACTION_HINTS)
    has_area = bool(_extract_area(raw))
    return bool(has_food and (has_action or has_area))


def parse_request(text: str, default_count: int = 30) -> ParsedRequest:
    raw = text.strip()
    if not looks_like_restaurant_message(raw):
        return ParsedRequest(intent="unknown", count=default_count)

    area = _extract_area(raw)

    topics: list[str] = []
    for key in TOPIC_HINTS:
        if key in raw and key not in topics:
            topics.append(key)
    topics = [
        topic
        for topic in topics
        if not any(topic != other and topic in other for other in topics)
    ]
    topic = " ".join(topics[:5])

    meal_type = ""
    for key in ("아침", "점심", "저녁", "야식", "술자리"):
        if key in raw:
            meal_type = key
            break

    budget = ""
    if "저렴" in raw or "싼" in raw:
        budget = "저렴"
    elif "비싼" in raw or "고급" in raw:
        budget = "비싼"

    occasion = ""
    for key in ("혼밥", "혼술", "데이트", "회식", "2차"):
        if key in raw:
            occasion = key
            break

    count = default_count
    count_match = re.search(r"(\d{1,2})\s*(?:개|곳|군데)", raw)
    if count_match:
        count = max(1, min(30, int(count_match.group(1))))

    return ParsedRequest(
        intent="start",
        area=area,
        topic=topic,
        meal_type=meal_type,
        budget=budget,
        occasion=occasion,
        count=count,
    )
