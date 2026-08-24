from __future__ import annotations

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
SPECIFIC_FOOD_TERMS = (
    "쭈꾸미",
    "무한리필",
    "무제한",
    "뷔페",
    "부페",
    "샤브샤브",
    "돼지국밥",
    "감자탕",
    "뼈해장국",
    "해장국",
    "국밥",
    "해장",
    "패스트푸드",
    "햄버거",
    "버거",
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
EXACT_FOOD_ALLOWLISTS = (
    (
        ("국밥", "돼지국밥", "순대국", "순댓국"),
        ("국밥", "돼지국", "순대국", "순댓국", "해장국", "뼈해장", "감자탕", "설렁탕", "곰탕"),
    ),
    (("감자탕", "뼈해장국"), ("감자탕", "뼈해장", "해장국")),
    (("초밥",), ("초밥", "스시", "일식")),
    (("샤브샤브",), ("샤브샤브", "월남쌈", "편백찜")),
    (("무한리필", "무제한", "뷔페", "부페"), ("무한리필", "무제한", "뷔페", "부페", "샐러드바", "리필")),
    (("고기",), ("고기", "갈비", "삼겹", "목살", "곱창", "구이")),
    (("카페", "커피", "커피집"), ("카페", "커피")),
)


def intent_allows_cafe(*parts: str) -> bool:
    text = " ".join(parts)
    return any(term in text for term in CAFE_INTENT_TERMS)


def intent_allows_fast_food(*parts: str) -> bool:
    text = " ".join(parts)
    return any(term in text for term in FAST_FOOD_INTENT_TERMS)


def exact_food_allowed_terms(topic: str) -> tuple[str, ...]:
    topic = topic.strip()
    if not topic:
        return ()
    for triggers, allowed_terms in EXACT_FOOD_ALLOWLISTS:
        if any(trigger in topic for trigger in triggers):
            return allowed_terms
    return ()


def should_apply_diversity_rerank(topic: str) -> bool:
    topic = topic.strip()
    if not topic or topic == "맛집":
        return True
    return not any(term in topic for term in SPECIFIC_FOOD_TERMS)


def is_excluded_general_text(
    text: str,
    allow_fast_food: bool = False,
    case_sensitive: bool = True,
) -> bool:
    excluded_name_words = GENERAL_EXCLUDED_NAME_WORDS
    excluded_category_words = GENERAL_EXCLUDED_CATEGORY_WORDS
    if allow_fast_food:
        excluded_name_words = tuple(
            word for word in excluded_name_words if word not in FAST_FOOD_EXCLUDED_NAME_WORDS
        )
        excluded_category_words = tuple(word for word in excluded_category_words if word != "패스트푸드")
    if not case_sensitive:
        text = text.lower()
        excluded_name_words = tuple(word.lower() for word in excluded_name_words)
        excluded_category_words = tuple(word.lower() for word in excluded_category_words)
    if any(word in text for word in excluded_name_words):
        return True
    return any(word in text for word in excluded_category_words)
