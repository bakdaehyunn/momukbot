from __future__ import annotations

import re
from html import unescape

from momukbot.core.matching import normalize_match_text
from momukbot.core.models import SearchCandidate


LOCAL_CANDIDATE_MULTIPLIER = 2
LOCAL_CANDIDATE_MAX = 60
LOCAL_CANDIDATE_EXPANDED_MULTIPLIER = 3
LOCAL_CANDIDATE_EXPANDED_MAX = 90

CAFE_INTENT_TERMS = ("카페", "커피", "커피집", "디저트", "베이커리", "빵")
FAST_FOOD_INTENT_TERMS = ("패스트푸드", "햄버거", "버거")
FAST_FOOD_EXCLUDED_NAME_WORDS = (
    "맥도날드",
    "버거킹",
    "롯데리아",
    "써브웨이",
    "서브웨이",
    "맘스터치",
    "KFC",
    "파파이스",
    "노브랜드버거",
)
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
    "KFC",
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


def clean_html(text: str) -> str:
    text = unescape(text or "")
    text = re.sub(r"</?b>", "", text)
    text = re.sub(r"<[^>]+>", "", text)
    return re.sub(r"\s+", " ", text).strip()


def _context_terms(context_hint: str) -> list[str]:
    terms: list[str] = []
    for token in re.split(r"[\s,/]+", context_hint.strip()):
        token = token.strip()
        if len(token) < 2:
            continue
        if token not in terms:
            terms.append(token)
    return terms[:3]


def _dedupe(items: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        if item in seen:
            continue
        seen.add(item)
        out.append(item)
    return out


def _local_candidate_queries(
    area: str,
    topic: str,
    count: int,
    context_hint: str = "",
    expanded: bool = False,
) -> list[str]:
    area = area.strip()
    topic = topic.strip()
    context_terms = _context_terms(context_hint)
    queries: list[str] = []
    if topic and topic != "맛집":
        queries.extend(
            [
                " ".join([area, topic]).strip(),
                " ".join([area, topic, "맛집"]).strip(),
                " ".join([area, "맛집", topic]).strip(),
            ]
        )
    else:
        queries.append(" ".join([area, "맛집"]).strip())

    for term in context_terms:
        queries.append(" ".join([area, "맛집", term]).strip())

    if topic and topic != "맛집" and not _allows_cafe_candidates(topic, context_hint):
        queries.extend(_same_intent_candidate_queries(area, topic))
    elif not _allows_cafe_candidates(topic, context_hint):
        queries.extend(
            [
                " ".join([area, "한식 맛집"]).strip(),
                " ".join([area, "고기 맛집"]).strip(),
                " ".join([area, "국수 맛집"]).strip(),
                " ".join([area, "일식 맛집"]).strip(),
                " ".join([area, "중식 맛집"]).strip(),
                " ".join([area, "양식 맛집"]).strip(),
                " ".join([area, "해장국"]).strip(),
                " ".join([area, "술집"]).strip(),
                " ".join([area, "점심 맛집"]).strip(),
                " ".join([area, "밥집"]).strip(),
                " ".join([area, "분식"]).strip(),
                " ".join([area, "족발"]).strip(),
                " ".join([area, "치킨"]).strip(),
                " ".join([area, "회식 맛집"]).strip(),
            ]
        )
        if expanded:
            queries.extend(_expanded_meal_candidate_queries(area))
    elif len(queries) < count:
        queries.extend(
            [
                " ".join([area, "카페"]).strip(),
                " ".join([area, "커피"]).strip(),
                " ".join([area, "디저트"]).strip(),
                " ".join([area, "베이커리"]).strip(),
            ]
        )
    return _dedupe([query for query in queries if query])


def _expanded_meal_candidate_queries(area: str) -> list[str]:
    return [
        " ".join([area, "백반"]).strip(),
        " ".join([area, "돈까스"]).strip(),
        " ".join([area, "돈카츠"]).strip(),
        " ".join([area, "찌개"]).strip(),
        " ".join([area, "냉면"]).strip(),
        " ".join([area, "칼국수"]).strip(),
        " ".join([area, "샤브샤브"]).strip(),
        " ".join([area, "초밥"]).strip(),
        " ".join([area, "덮밥"]).strip(),
        " ".join([area, "라멘"]).strip(),
        " ".join([area, "곱창"]).strip(),
        " ".join([area, "닭갈비"]).strip(),
        " ".join([area, "보쌈"]).strip(),
        " ".join([area, "식당"]).strip(),
    ]


def _same_intent_candidate_queries(area: str, topic: str) -> list[str]:
    text = topic.strip()
    groups: tuple[tuple[tuple[str, ...], tuple[str, ...]], ...] = (
        (
            ("국밥", "해장", "순대국", "순댓국", "감자탕", "설렁탕", "곰탕"),
            ("국밥", "순대국", "순댓국", "순대국밥", "돼지국밥", "해장국", "뼈해장국", "감자탕", "설렁탕", "곰탕"),
        ),
        (
            ("초밥", "스시", "일식", "라멘", "우동", "돈카츠", "돈까스"),
            ("일식", "초밥", "스시", "회전초밥", "사시미", "라멘", "우동", "돈카츠", "돈까스"),
        ),
        (
            ("중식", "마라", "마라탕", "짬뽕", "짜장", "양꼬치"),
            ("중식", "마라탕", "마라샹궈", "짬뽕", "짜장면", "양꼬치"),
        ),
        (
            ("고기", "삼겹", "갈비", "소고기", "돼지고기", "구이"),
            ("고기 맛집", "삼겹살", "목살", "갈비", "소고기", "돼지고기", "곱창", "구이"),
        ),
        (("술", "혼술", "술집", "이자카야", "포차", "맥주"), ("술집", "이자카야", "요리주점")),
        (("패스트푸드", "햄버거", "버거"), ("패스트푸드", "햄버거", "버거")),
        (("분식", "떡볶이", "김밥"), ("분식", "떡볶이", "김밥")),
        (("치킨", "닭"), ("치킨", "닭요리")),
        (("족발", "보쌈"), ("족발", "보쌈")),
        (("파스타", "양식", "스테이크", "피자"), ("양식", "파스타", "스테이크")),
    )
    for needles, expansions in groups:
        if any(needle in text for needle in needles):
            return [" ".join([area, expansion]).strip() for expansion in expansions]
    return []


def _allows_cafe_candidates(topic: str, context_hint: str = "") -> bool:
    text = " ".join([topic, context_hint])
    return any(term in text for term in CAFE_INTENT_TERMS)


def _allows_fast_food_candidates(topic: str, context_hint: str = "") -> bool:
    text = " ".join([topic, context_hint])
    return any(term in text for term in FAST_FOOD_INTENT_TERMS)


def _local_candidate_target_count(count: int, expanded: bool = False) -> int:
    requested = max(1, count)
    if expanded:
        return min(
            LOCAL_CANDIDATE_EXPANDED_MAX,
            max(requested, requested * LOCAL_CANDIDATE_EXPANDED_MULTIPLIER),
        )
    return min(LOCAL_CANDIDATE_MAX, max(requested, requested * LOCAL_CANDIDATE_MULTIPLIER))


def _candidate_category(name: str, category: str) -> str:
    text = f"{name} {category}"
    if any(word in text for word in ("무한리필", "무제한", "뷔페", "부페", "샐러드바")):
        return "무한리필"
    if any(word in text for word in ("샤브샤브", "월남쌈", "편백찜")):
        return "샤브샤브"
    if any(word in text for word in ("카페", "커피", "디저트", "베이커리", "제과", "제빵", "빵")):
        return "카페"
    if any(word in text for word in ("국밥", "순대국", "순댓국")):
        return "국밥"
    if "감자탕" in text:
        return "감자탕"
    if any(word in text for word in ("해장국", "설렁탕", "곰탕")):
        return "해장국"
    if any(word in text for word in ("술집", "주점", "포차", "맥주", "이자카야", "와인")):
        return "술집"
    if any(word in text for word in ("일식", "초밥", "스시", "참치", "우동", "라멘", "돈카츠", "돈까스")):
        return "일식"
    if any(word in text for word in ("중식", "중국", "마라", "짬뽕", "짜장", "양꼬치")):
        return "중식"
    if any(
        word in text
        for word in (
            "한식",
            "고기",
            "갈비",
            "삼겹",
            "국수",
            "냉면",
            "백반",
            "분식",
            "족발",
            "보쌈",
            "곱창",
            "찌개",
            "구이",
            "닭",
            "치킨",
        )
    ):
        return "한식"
    return "기타"


def _is_excluded_general_candidate(candidate: SearchCandidate, allow_fast_food: bool = False) -> bool:
    text = f"{candidate.name} {candidate.category} {candidate.raw_category}"
    excluded_name_words = GENERAL_EXCLUDED_NAME_WORDS
    excluded_category_words = GENERAL_EXCLUDED_CATEGORY_WORDS
    if allow_fast_food:
        excluded_name_words = tuple(
            word for word in excluded_name_words if word not in FAST_FOOD_EXCLUDED_NAME_WORDS
        )
        excluded_category_words = tuple(word for word in excluded_category_words if word != "패스트푸드")
    if any(word in text for word in excluded_name_words):
        return True
    return any(word in text for word in excluded_category_words)


def _candidate_key(candidate: SearchCandidate) -> str:
    return normalize_match_text(candidate.name)
