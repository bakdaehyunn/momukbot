from __future__ import annotations

import re

from .models import ParsedRequest


FOOD_HINTS = (
    "맛집",
    "밥",
    "식당",
    "야식",
    "술집",
    "펍",
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
    "뭐 먹",
    "뭐먹",
)
ACTION_HINTS = ("추천", "찾아", "알려", "보여", "가기 좋은", "갈만한", "뭐", "어디", "위주")
TOPIC_HINTS = (
    "쭈꾸미",
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
    "혼밥",
    "회식",
    "카페",
    "바",
)


def looks_like_restaurant_message(text: str) -> bool:
    raw = text.strip()
    if not raw:
        return False
    if raw.startswith(("/맛집", "/momuk", "/뭐먹")):
        return True
    has_food = any(key in raw for key in FOOD_HINTS)
    has_action = any(key in raw for key in ACTION_HINTS)
    has_area = re.search(r"[가-힣A-Za-z0-9]+(?:역|동|로|구|시|면|거리)\s*(?:에서|근처|기준|주변)", raw)
    return bool(has_food and (has_action or has_area))


def parse_request(text: str, default_count: int = 30) -> ParsedRequest:
    raw = text.strip()
    if not looks_like_restaurant_message(raw):
        return ParsedRequest(intent="unknown", count=default_count)

    area = ""
    area_match = re.search(r"([가-힣A-Za-z0-9]+(?:역|동|로|구|시|면|입구|거리)?)\s*(?:에서|근처|기준|주변)", raw)
    if area_match:
        area = area_match.group(1).strip()
    elif raw.startswith(("/맛집", "/momuk", "/뭐먹")):
        parts = raw.split(maxsplit=2)
        if len(parts) >= 2:
            area = parts[1].strip()
    else:
        simple_area = re.search(r"([가-힣A-Za-z0-9]+(?:역|동|로|구|시|면))", raw)
        if simple_area:
            area = simple_area.group(1).strip()

    topics: list[str] = []
    for key in TOPIC_HINTS:
        if key in raw and key not in topics:
            topics.append(key)
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
    for key in ("혼밥", "데이트", "회식", "2차"):
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
