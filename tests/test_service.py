from pathlib import Path

from momukbot.config import Settings
from momukbot.core.models import SearchContext
from momukbot.core.service import RecommendationService, parse_recommendation


class FakeAgent:
    def generate(self, prompt: str) -> str:
        return """
        {
          "search_keyword": "서면 해장",
          "items": [
            {
              "name": "송정3대국밥",
              "category": "국밥",
              "status_marker": "영업 가능성 높음",
              "reason": "블로그 후기가 많습니다.",
              "links": [{"label": "리뷰", "url": "https://blog.naver.com/a/b"}]
            }
          ]
        }
        """


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
        return SearchContext(
            text=f"1. title=서면 국밥 url=https://blog.naver.com/a/b context_hint={context_hint}",
            used_provider="fake",
            configured=True,
        )


def settings(tmp_path: Path) -> Settings:
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
    )


def test_parse_recommendation_json() -> None:
    result = parse_recommendation(FakeAgent().generate("x"))

    assert result.search_keyword == "서면 해장"
    assert result.items[0].name == "송정3대국밥"
    assert result.items[0].links[0]["label"] in {"블로그", "리뷰"}


def test_service_dry_run_does_not_call_agent(tmp_path: Path) -> None:
    service = RecommendationService(settings(tmp_path), FakeAgent(), FakeSearch())
    response = service.handle_text("cli", "서면에서 해장 국밥 추천해줘", dry_run=True)

    assert response is not None
    assert "dry-run" in response
    assert "실제 AI 에이전트 호출은 하지 않았습니다" in response
    assert "Need: 30 recommendations" in response
    assert "Return exactly 30 items" in response
    assert "Do not use Tistory" in response
    assert "tistory.com" not in response
    assert "Naver Blog" in response


def test_service_formats_agent_response(tmp_path: Path) -> None:
    service = RecommendationService(settings(tmp_path), FakeAgent(), FakeSearch())
    response = service.handle_text("cli", "서면에서 해장 국밥 추천해줘")

    assert response is not None
    assert "송정3대국밥" in response
    assert "네이버지도:" in response
