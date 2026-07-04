from momukbot.core.llm_parser import LLMRequestParser, parse_llm_request, route_decision
from momukbot.core.parser import parse_request


class RecordingAgent:
    def __init__(self, response: str) -> None:
        self.response = response
        self.prompts: list[str] = []

    def generate(self, prompt: str) -> str:
        self.prompts.append(prompt)
        return self.response


class CorpusAgent:
    def __init__(self) -> None:
        self.prompts: list[str] = []
        self.responses = {
            "오목교역 곱창 맛집 추천": '{"intent":"restaurant_recommendation","area":"오목교역","topic":"곱창","count":30,"needs_location":false}',
            "목동역 늦게까지 하는 고기집 추천": '{"intent":"restaurant_recommendation","area":"목동역","topic":"고기","count":30,"needs_location":false}',
            "오늘 근처에 먹을만한 곳": '{"intent":"needs_location","area":"","topic":"맛집","count":30,"needs_location":true}',
            "괜찮은 식당 있나": '{"intent":"restaurant_recommendation","area":"","topic":"식당","count":30,"needs_location":false}',
            "오늘 뭐 먹지": '{"intent":"needs_location","area":"","topic":"맛집","count":30,"needs_location":true}',
            "이태원역 한잔하기 좋은 곳": '{"intent":"restaurant_recommendation","area":"이태원역","topic":"술집","occasion":"한잔","count":30,"needs_location":false}',
            "강남역 조용한 파스타집 알려줘": '{"intent":"restaurant_recommendation","area":"강남역","topic":"파스타","count":30,"needs_location":false}',
            "홍대입구 2차로 갈만한 이자카야": '{"intent":"restaurant_recommendation","area":"홍대입구","topic":"이자카야","occasion":"2차","count":30,"needs_location":false}',
        }

    def generate(self, prompt: str) -> str:
        self.prompts.append(prompt)
        text = prompt.rsplit("User message:", 1)[-1].strip()
        return self.responses[text]


def test_router_realistic_korean_message_corpus() -> None:
    cases = [
        {
            "text": "오목교역 곱창 맛집 추천",
            "source": "llm",
            "reason": "suspicious_area_food_term",
            "intent": "start",
            "area": "오목교역",
            "topic": "곱창",
            "llm_used": True,
        },
        {
            "text": "목동역 늦게까지 하는 고기집 추천",
            "source": "llm",
            "reason": "suspicious_area_descriptor",
            "intent": "start",
            "area": "목동역",
            "topic": "고기",
            "llm_used": True,
        },
        {
            "text": "오늘 근처에 먹을만한 곳",
            "source": "llm",
            "reason": "unknown_foodish",
            "intent": "needs_location",
            "area": "",
            "topic": "맛집",
            "llm_used": True,
        },
        {
            "text": "괜찮은 식당 있나",
            "source": "llm",
            "reason": "suspicious_area_descriptor",
            "intent": "start",
            "area": "",
            "topic": "식당",
            "llm_used": True,
        },
        {
            "text": "이태원에서 한잔할 술집 추천",
            "source": "rule",
            "reason": "rule_confident",
            "intent": "start",
            "area": "이태원",
            "topic": "술집",
            "llm_used": False,
        },
        {
            "text": "연남동 카페 추천",
            "source": "rule",
            "reason": "rule_confident",
            "intent": "start",
            "area": "연남동",
            "topic": "카페",
            "llm_used": False,
        },
        {
            "text": "목동역 패스트푸드 추천",
            "source": "rule",
            "reason": "rule_confident",
            "intent": "start",
            "area": "목동역",
            "topic": "패스트푸드",
            "llm_used": False,
        },
        {
            "text": "오늘 뭐 먹지",
            "source": "llm",
            "reason": "suspicious_area_descriptor",
            "intent": "needs_location",
            "area": "",
            "topic": "맛집",
            "llm_used": True,
        },
        {
            "text": "이태원역 한잔하기 좋은 곳",
            "source": "llm",
            "reason": "unknown_foodish",
            "intent": "start",
            "area": "이태원역",
            "topic": "술집",
            "occasion": "한잔",
            "llm_used": True,
        },
        {
            "text": "안녕 뭐해",
            "source": "rule",
            "reason": "unknown_not_foodish",
            "intent": "unknown",
            "area": "",
            "topic": "",
            "llm_used": False,
        },
        {
            "text": "서울 맛집 데이터 정리해줘",
            "source": "rule",
            "reason": "work_request",
            "intent": "unknown",
            "area": "",
            "topic": "",
            "llm_used": False,
        },
        {
            "text": "강남역 조용한 파스타집 알려줘",
            "source": "llm",
            "reason": "unknown_foodish",
            "intent": "start",
            "area": "강남역",
            "topic": "파스타",
            "llm_used": True,
        },
        {
            "text": "홍대입구 2차로 갈만한 이자카야",
            "source": "llm",
            "reason": "suspicious_area_descriptor",
            "intent": "start",
            "area": "홍대입구",
            "topic": "이자카야",
            "occasion": "2차",
            "llm_used": True,
        },
    ]
    agent = CorpusAgent()
    parser = LLMRequestParser(agent)

    for case in cases:
        fallback = parse_request(case["text"])
        result = parser.parse_with_metadata(case["text"], fallback)

        assert result.source == case["source"], case["text"]
        assert result.reason == case["reason"], case["text"]
        assert result.llm_used is case["llm_used"], case["text"]
        assert result.parsed.intent == case["intent"], case["text"]
        assert result.parsed.area == case["area"], case["text"]
        assert result.parsed.topic == case["topic"], case["text"]
        if "occasion" in case:
            assert result.parsed.occasion == case["occasion"], case["text"]

    assert len(agent.prompts) == sum(1 for case in cases if case["llm_used"])


def test_llm_parser_repairs_area_swallowed_food_topic() -> None:
    agent = RecordingAgent(
        """
        {
          "intent": "restaurant_recommendation",
          "area": "오목교역",
          "topic": "곱창",
          "meal_type": "",
          "budget": "",
          "occasion": "",
          "count": 30,
          "needs_location": false
        }
        """
    )
    fallback = parse_request("오목교역 곱창 맛집 추천")

    result = LLMRequestParser(agent).parse_with_metadata("오목교역 곱창 맛집 추천", fallback)
    parsed = result.parsed

    assert parsed.intent == "start"
    assert parsed.area == "오목교역"
    assert parsed.topic == "곱창"
    assert result.source == "llm"
    assert result.reason == "suspicious_area_food_term"
    assert result.llm_used is True
    assert len(agent.prompts) == 1


def test_llm_parser_preserves_default_count_when_user_did_not_ask_for_count() -> None:
    agent = RecordingAgent(
        """
        {
          "intent": "restaurant_recommendation",
          "area": "오목교역",
          "topic": "곱창",
          "count": 1,
          "needs_location": false
        }
        """
    )
    fallback = parse_request("오목교역 곱창 맛집 추천")

    parsed = LLMRequestParser(agent).parse("오목교역 곱창 맛집 추천", fallback)

    assert parsed.count == 30


def test_llm_parser_keeps_explicit_user_count() -> None:
    agent = RecordingAgent(
        """
        {
          "intent": "restaurant_recommendation",
          "area": "오목교역",
          "topic": "곱창",
          "count": 3,
          "needs_location": false
        }
        """
    )
    fallback = parse_request("오목교역 곱창 맛집 3곳 추천")

    parsed = LLMRequestParser(agent).parse("오목교역 곱창 맛집 3곳 추천", fallback)

    assert parsed.count == 3


def test_llm_parser_repairs_descriptor_swallowed_into_area() -> None:
    agent = RecordingAgent(
        """
        {
          "intent": "restaurant_recommendation",
          "area": "목동역",
          "topic": "고기",
          "meal_type": "",
          "budget": "",
          "occasion": "",
          "count": 30,
          "needs_location": false
        }
        """
    )
    fallback = parse_request("목동역 늦게까지 하는 고기집 추천")

    parsed = LLMRequestParser(agent).parse("목동역 늦게까지 하는 고기집 추천", fallback)

    assert parsed.intent == "start"
    assert parsed.area == "목동역"
    assert parsed.topic == "고기"


def test_llm_parser_handles_unknown_foodish_free_text() -> None:
    agent = RecordingAgent(
        """
        {
          "intent": "restaurant_recommendation",
          "area": "",
          "topic": "맛집",
          "meal_type": "",
          "budget": "",
          "occasion": "",
          "count": 30,
          "needs_location": false
        }
        """
    )
    fallback = parse_request("오늘 근처에 먹을만한 곳")

    result = LLMRequestParser(agent).parse_with_metadata("오늘 근처에 먹을만한 곳", fallback)

    assert result.source == "llm"
    assert result.reason == "unknown_foodish"
    assert result.parsed.intent == "start"
    assert result.parsed.topic == "맛집"


def test_llm_parser_repairs_descriptor_only_area() -> None:
    agent = RecordingAgent(
        """
        {
          "intent": "restaurant_recommendation",
          "area": "",
          "topic": "식당",
          "meal_type": "",
          "budget": "",
          "occasion": "",
          "count": 30,
          "needs_location": false
        }
        """
    )
    fallback = parse_request("괜찮은 식당 있나")

    result = LLMRequestParser(agent).parse_with_metadata("괜찮은 식당 있나", fallback)

    assert result.source == "llm"
    assert result.reason == "suspicious_area_descriptor"
    assert result.parsed.intent == "start"
    assert result.parsed.area == ""
    assert result.parsed.topic == "식당"


def test_llm_parser_keeps_current_location_request_on_rule_parser_path() -> None:
    agent = RecordingAgent('{"intent": "unknown"}')
    fallback = parse_request("내 주변 한식 추천")

    result = LLMRequestParser(agent).parse_with_metadata("내 주변 한식 추천", fallback)
    parsed = result.parsed

    assert parsed.intent == "needs_location"
    assert parsed.topic == "한식"
    assert result.source == "rule"
    assert result.reason == "rule_confident"
    assert agent.prompts == []


def test_llm_parser_keeps_non_food_work_message_ignored() -> None:
    agent = RecordingAgent(
        '{"intent": "restaurant_recommendation", "area": "서울", "topic": "맛집"}'
    )
    fallback = parse_request("서울 맛집 데이터 정리해줘")

    parsed = LLMRequestParser(agent).parse("서울 맛집 데이터 정리해줘", fallback)

    assert parsed.intent == "unknown"
    assert agent.prompts == []


def test_llm_parser_can_be_disabled() -> None:
    agent = RecordingAgent(
        '{"intent": "restaurant_recommendation", "area": "오목교역", "topic": "곱창"}'
    )
    fallback = parse_request("오목교역 곱창 맛집 추천")

    result = LLMRequestParser(agent, enabled=False).parse_with_metadata(
        "오목교역 곱창 맛집 추천",
        fallback,
    )

    assert result.source == "rule"
    assert result.reason == "disabled"
    assert result.parsed.area == "오목교역 곱창"
    assert agent.prompts == []


def test_route_decision_keeps_obvious_work_requests_out_of_llm() -> None:
    parsed = parse_request("서울 맛집 데이터 정리해줘")

    decision = route_decision("서울 맛집 데이터 정리해줘", parsed)

    assert decision.use_llm is False
    assert decision.reason == "work_request"


def test_parse_llm_request_rejects_malformed_output() -> None:
    assert parse_llm_request("not json") is None
