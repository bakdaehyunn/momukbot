from momukbot.core.parser import parse_request


def test_parse_area_and_topic() -> None:
    parsed = parse_request("서면에서 해장할 건데 국밥 감자탕 위주로 추천해줘")

    assert parsed.intent == "start"
    assert parsed.area == "서면"
    assert "국밥" in parsed.topic
    assert "감자탕" in parsed.topic


def test_parse_unknown() -> None:
    parsed = parse_request("오늘 회의록 정리해줘")

    assert parsed.intent == "unknown"


def test_parse_area_topic_without_particle() -> None:
    parsed = parse_request("목동역 맛집 추천")

    assert parsed.intent == "start"
    assert parsed.area == "목동역"
    assert "맛집" in parsed.topic


def test_parse_honsul_bar_without_particle() -> None:
    parsed = parse_request("이태원 혼술바 추천")

    assert parsed.intent == "start"
    assert parsed.area == "이태원"
    assert parsed.topic == ""
    assert parsed.occasion == "혼술"


def test_parse_common_korean_area_shapes() -> None:
    examples = {
        "강남역 맛집 추천": "강남역",
        "홍대입구 혼밥 추천": "홍대입구",
        "성수동 데이트 맛집": "성수동",
        "부산 서면 국밥 추천": "부산 서면",
        "대구 동성로 맛집 추천": "대구 동성로",
        "수원역 점심 추천": "수원역",
        "전주 한옥마을 맛집 추천": "전주 한옥마을",
        "해운대 해수욕장 근처 맛집": "해운대 해수욕장",
        "제주공항 근처 밥집 추천": "제주공항",
        "인천 송도 센트럴파크 맛집 추천": "인천 송도 센트럴파크",
    }

    for text, expected_area in examples.items():
        parsed = parse_request(text)
        assert parsed.intent == "start", text
        assert parsed.area == expected_area, text


def test_parse_does_not_treat_non_food_work_request_as_area() -> None:
    parsed = parse_request("부산 프로젝트 회의록 정리해줘")

    assert parsed.intent == "unknown"
