from datetime import date
from pathlib import Path

from momukbot.config import Settings
from momukbot.search.naver import NaverSearchProvider, build_blog_evidence, score_blog_evidence


def settings(tmp_path: Path) -> Settings:
    return Settings(
        telegram_bot_token="",
        telegram_allowed_chat_ids=(),
        naver_client_id="client",
        naver_client_secret="secret",
        naver_daily_soft_limit=10,
        blog_allowed_domains=("blog.naver.com", "tistory.com"),
        agent_provider="codex_cli",
        codex_bin="codex",
        codex_workdir=tmp_path,
        codex_sandbox="read-only",
        codex_timeout_sec=60,
        default_count=30,
        state_dir=tmp_path,
        log_dir=tmp_path,
    )


def test_score_blog_evidence_prefers_recent_matching_visit_review() -> None:
    score, signals, penalties = score_blog_evidence(
        title="서면 국밥 맛집 방문 후기",
        summary="직접 다녀왔고 주문한 돼지국밥이 좋았습니다. 24시 영업.",
        postdate="20260420",
        area="서면",
        topic="해장 국밥",
        today=date(2026, 5, 1),
    )

    assert score >= 12
    assert "recent_90d" in signals
    assert "title_match:서면" in signals
    assert "title_match:국밥" in signals
    assert "visit:방문" in signals
    assert "open_hint:24시" in signals
    assert penalties == []


def test_score_blog_evidence_penalizes_ad_like_roundup_without_visit_signal() -> None:
    score, signals, penalties = score_blog_evidence(
        title="서면역 맛집 추천 BEST 모음",
        summary="원고료를 제공받아 작성한 리스트입니다.",
        postdate="20230101",
        area="서면",
        topic="맛집",
        today=date(2026, 5, 1),
    )

    assert score < 0
    assert "old_post" in penalties
    assert "ad_like:원고료" in penalties
    assert "roundup:BEST" in penalties
    assert any(signal.startswith("title_match:") for signal in signals)


def test_build_blog_evidence_cleans_html_and_keeps_score() -> None:
    evidence = build_blog_evidence(
        {
            "title": "<b>서면</b> 국밥",
            "description": "직접 방문한 후기",
            "postdate": "20260430",
            "bloggername": "블로거",
            "link": "https://blog.naver.com/a/b",
        },
        area="서면",
        topic="국밥",
        today=date(2026, 5, 1),
    )

    assert evidence.title == "서면 국밥"
    assert evidence.score > 0
    assert evidence.url == "https://blog.naver.com/a/b"


def test_build_context_orders_blog_evidence_by_score(tmp_path: Path) -> None:
    provider = NaverSearchProvider(settings(tmp_path))

    def fake_search(endpoint: str, query: str, display: int = 10, sort: str = "sim"):
        if endpoint == "blog":
            return {
                "items": [
                    {
                        "title": "오래된 서면 맛집 BEST",
                        "description": "광고 리스트",
                        "postdate": "20220101",
                        "bloggername": "old",
                        "link": "https://blog.naver.com/old/post",
                    },
                    {
                        "title": "서면 국밥 방문 후기",
                        "description": "직접 다녀왔고 24시 영업이라 해장하기 좋았습니다.",
                        "postdate": "20260420",
                        "bloggername": "recent",
                        "link": "https://blog.naver.com/recent/post",
                    },
                ]
            }
        return {"items": []}

    provider.search = fake_search  # type: ignore[method-assign]

    context = provider.build_context("서면", "해장 국밥", count=2).text

    assert "score=" in context
    assert context.find("blogger=recent") < context.find("blogger=old")
