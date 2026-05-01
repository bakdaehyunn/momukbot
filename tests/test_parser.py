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
