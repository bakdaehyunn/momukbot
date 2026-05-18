import logging
from pathlib import Path

from momukbot.config import Settings
from momukbot.core.models import SearchContext
from momukbot.core.service import RecommendationService, parse_recommendation


class FakeAgent:
    def generate(self, prompt: str) -> str:
        return recommendation_json(30)


class UnderfilledAgent:
    def __init__(self) -> None:
        self.prompts: list[str] = []

    def generate(self, prompt: str) -> str:
        self.prompts.append(prompt)
        return recommendation_json(13)


class CafeMixedAgent:
    def __init__(self) -> None:
        self.prompts: list[str] = []

    def generate(self, prompt: str) -> str:
        self.prompts.append(prompt)
        return recommendation_json(30, first_name="스타벅스 목동역점", first_category="카페")


class MismatchedBlogAgent:
    def __init__(self) -> None:
        self.prompts: list[str] = []

    def generate(self, prompt: str) -> str:
        self.prompts.append(prompt)
        return recommendation_json(30, first_name="전혀다른가게", first_category="국밥")


class RawTextAgent:
    def generate(self, prompt: str) -> str:
        return "not valid recommendation json"


class FakeSearch:
    def __init__(self) -> None:
        self.context_hint = ""

    def build_context(
        self,
        area: str,
        topic: str,
        count: int = 30,
        context_hint: str = "",
    ) -> SearchContext:
        self.context_hint = context_hint
        evidence_lines = []
        for index in range(1, count + 1):
            name = "송정3대국밥" if index == 1 else f"서면국밥{index}"
            evidence_lines.append(
                f"{index}. title={name} 방문 후기 url=https://blog.naver.com/a/{index} summary={name} 블로그 후기"
            )
        return SearchContext(
            text=f"Naver Blog evidence context_hint={context_hint}\n" + "\n".join(evidence_lines),
            used_provider="fake",
            configured=True,
        )


class NoEvidenceSearch(FakeSearch):
    def build_context(
        self,
        area: str,
        topic: str,
        count: int = 30,
        context_hint: str = "",
    ) -> SearchContext:
        self.context_hint = context_hint
        return SearchContext(
            text="Naver API quota is blocked.",
            used_provider="fake",
            configured=True,
            quota_blocked=True,
            evidence_available=False,
        )


class BloglessSearch(FakeSearch):
    def build_context(
        self,
        area: str,
        topic: str,
        count: int = 30,
        context_hint: str = "",
    ) -> SearchContext:
        self.context_hint = context_hint
        return SearchContext(
            text="Naver Blog search returned no usable evidence.",
            used_provider="fake",
            configured=True,
            evidence_available=False,
        )


class RecordingStore:
    def __init__(self) -> None:
        self.raw_response = ""
        self.item_count = 0
        self.add_count = 0

    def add_result(
        self,
        chat_id: str,
        request_text: str,
        area: str,
        topic: str,
        search_keyword: str,
        raw_response: str,
        items,
    ) -> None:
        self.raw_response = raw_response
        self.item_count = len(items)
        self.add_count += 1


def recommendation_json(
    count: int,
    first_name: str = "송정3대국밥",
    first_category: str = "국밥",
) -> str:
    items: list[str] = []
    for index in range(1, count + 1):
        name = first_name if index == 1 else f"서면국밥{index}"
        category = first_category if index == 1 else "국밥"
        items.append(
            f"""{{
              "name": "{name}",
              "category": "{category}",
              "status_marker": "영업 가능성 높음",
              "reason": "블로그 후기가 많습니다.",
              "links": [{{"label": "리뷰", "url": "https://blog.naver.com/a/{index}"}}]
            }}"""
        )
    return f"""
    {{
      "search_keyword": "서면 해장",
      "items": [{",".join(items)}]
    }}
    """


def settings(tmp_path: Path, store_raw_response: bool = False) -> Settings:
    return Settings(
        telegram_bot_token="",
        telegram_allowed_chat_ids=(),
        telegram_admin_user_ids=(),
        naver_client_id="",
        naver_client_secret="",
        naver_daily_soft_limit=10,
        blog_allowed_domains=("blog.naver.com",),
        agent_provider="codex_cli",
        codex_bin="codex",
        codex_workdir=tmp_path,
        codex_sandbox="read-only",
        codex_timeout_sec=60,
        default_count=30,
        state_dir=tmp_path,
        log_dir=tmp_path,
        store_raw_response=store_raw_response,
    )


def test_parse_recommendation_json() -> None:
    result = parse_recommendation(FakeAgent().generate("x"))

    assert result.search_keyword == "서면 해장"
    assert result.items[0].name == "송정3대국밥"
    assert result.items[0].links[0]["label"] == "네이버 블로그"


def test_service_dry_run_does_not_call_agent(tmp_path: Path) -> None:
    service = RecommendationService(settings(tmp_path), FakeAgent(), FakeSearch())
    response = service.handle_text("cli", "서면에서 해장 국밥 추천해줘", dry_run=True)

    assert response is not None
    assert "dry-run" in response
    assert "실제 AI 에이전트 호출은 하지 않았습니다" in response
    assert "Need: up to 30 recommendations" in response
    assert "Use open-status markers only when supported by the provided context" in response
    assert "Return up to 30 items" in response
    assert "Do not return fewer items because operating hours are uncertain" in response
    assert "stay within the same food intent" in response
    assert "Do not use Tistory" in response
    assert "do not perform any additional web searches" in response
    assert "Every returned item must include at least one Naver Blog URL copied" in response
    assert "must be from evidence whose title or summary names that exact place" in response
    assert "deterministic local candidate roster" not in response
    assert "Do not search again just to verify operating hours" in response
    assert 'General "맛집" means meal-serving restaurants' in response
    assert "Exclude cafes, coffee chains, dessert-only shops" in response
    assert '"category": "국밥|감자탕|해장국|술집|일식|중식|한식|기타"' in response
    assert "tistory.com" not in response
    assert "Naver Blog" in response


def test_service_dry_run_allows_cafe_for_explicit_coffee_request(tmp_path: Path) -> None:
    service = RecommendationService(settings(tmp_path), FakeAgent(), FakeSearch())
    response = service.handle_text("cli", "목동역 커피 추천", dry_run=True)

    assert response is not None
    assert "area=목동역" in response
    assert "topic=커피" in response
    assert "The user explicitly asked for cafe/coffee/dessert/bakery" in response
    assert '"category": "국밥|감자탕|해장국|술집|카페|일식|중식|한식|기타"' in response


def test_service_formats_agent_response(tmp_path: Path) -> None:
    service = RecommendationService(settings(tmp_path), FakeAgent(), FakeSearch())
    response = service.handle_text("cli", "서면에서 해장 국밥 추천해줘")

    assert response is not None
    assert "송정3대국밥" in response
    assert "네이버 지도:" in response


def test_service_does_not_call_agent_when_naver_evidence_is_unavailable(tmp_path: Path) -> None:
    agent = UnderfilledAgent()
    store = RecordingStore()
    service = RecommendationService(settings(tmp_path), agent, NoEvidenceSearch(), store)  # type: ignore[arg-type]

    response = service.handle_text("cli", "서면에서 해장 국밥 추천해줘")

    assert response == "Naver 근거를 가져오지 못했어요. 오늘 Naver API 한도 상태를 확인한 뒤 다시 시도해주세요."
    assert agent.prompts == []
    assert store.add_count == 0


def test_service_sends_confirmed_partial_result_without_completion_retry(tmp_path: Path) -> None:
    agent = UnderfilledAgent()
    store = RecordingStore()
    service = RecommendationService(settings(tmp_path), agent, FakeSearch(), store)  # type: ignore[arg-type]

    response = service.handle_text("cli", "서면에서 해장 국밥 추천해줘")

    assert response is not None
    assert response.startswith("네이버 블로그 근거가 확인된 13곳만 보여드려요.")
    assert "서면 해장 추천 13곳" in response
    assert len(agent.prompts) == 1
    assert store.item_count == 13
    assert store.add_count == 1


def test_service_rejects_local_candidate_fallback_without_blog_evidence(tmp_path: Path) -> None:
    agent = UnderfilledAgent()
    store = RecordingStore()
    service = RecommendationService(settings(tmp_path), agent, BloglessSearch(), store)  # type: ignore[arg-type]

    response = service.handle_text("cli", "목동역 맛집 추천")

    assert response == "Naver 근거를 충분히 가져오지 못했어요. 잠시 후 다시 시도해주세요."
    assert agent.prompts == []
    assert store.add_count == 0


def test_service_filters_cafe_results_for_general_restaurant_request(tmp_path: Path) -> None:
    agent = CafeMixedAgent()
    store = RecordingStore()
    service = RecommendationService(settings(tmp_path), agent, FakeSearch(), store)  # type: ignore[arg-type]

    response = service.handle_text("cli", "목동역 맛집 추천")

    assert response is not None
    assert "스타벅스" not in response
    assert response.startswith("네이버 블로그 근거가 확인된 29곳만 보여드려요.")
    assert len(agent.prompts) == 1
    assert store.item_count == 29
    assert store.add_count == 1


def test_service_rejects_blog_link_for_different_place(tmp_path: Path) -> None:
    agent = MismatchedBlogAgent()
    store = RecordingStore()
    service = RecommendationService(settings(tmp_path), agent, FakeSearch(), store)  # type: ignore[arg-type]

    response = service.handle_text("cli", "서면에서 해장 국밥 추천해줘")

    assert response is not None
    assert response.startswith("네이버 블로그 근거가 확인된 29곳만 보여드려요.")
    assert len(agent.prompts) == 1
    assert store.item_count == 29
    assert store.add_count == 1


def test_service_logs_stage_timings_with_masked_chat_id(tmp_path: Path, caplog) -> None:
    logger = logging.getLogger("momukbot.test.service_timing")
    service = RecommendationService(settings(tmp_path), FakeAgent(), FakeSearch(), logger=logger)

    with caplog.at_level(logging.INFO, logger=logger.name):
        response = service.handle_text("1234567890", "서면에서 해장 국밥 추천해줘")

    assert response is not None
    messages = [record.getMessage() for record in caplog.records if record.name == logger.name]
    for stage in (
        "parse",
        "normalize",
        "search_context",
        "prompt_build",
        "agent_generate",
        "response_parse",
        "store",
        "format",
        "total",
    ):
        assert any(f"stage={stage}" in message for message in messages)
    assert any("chat_id=***7890" in message for message in messages)
    assert all("1234567890" not in message for message in messages)
    assert any("context_chars=" in message for message in messages)
    assert any("prompt_chars=" in message for message in messages)
    assert any("raw_chars=" in message for message in messages)


def test_service_hides_raw_agent_text_when_recommendation_json_fails(tmp_path: Path) -> None:
    service = RecommendationService(settings(tmp_path), RawTextAgent(), FakeSearch())
    response = service.handle_text("cli", "서면에서 해장 국밥 추천해줘")

    assert response == "추천 결과 형식을 정리하지 못했어요. 잠시 후 다시 시도해주세요."


def test_service_does_not_store_raw_response_by_default(tmp_path: Path) -> None:
    store = RecordingStore()
    service = RecommendationService(settings(tmp_path), FakeAgent(), FakeSearch(), store)  # type: ignore[arg-type]

    service.handle_text("cli", "서면에서 해장 국밥 추천해줘")

    assert store.raw_response == ""


def test_service_stores_raw_response_when_enabled(tmp_path: Path) -> None:
    store = RecordingStore()
    service = RecommendationService(
        settings(tmp_path, store_raw_response=True),
        FakeAgent(),
        FakeSearch(),
        store,  # type: ignore[arg-type]
    )

    service.handle_text("cli", "서면에서 해장 국밥 추천해줘")

    assert "search_keyword" in store.raw_response
