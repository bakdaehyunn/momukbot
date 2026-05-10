from datetime import date
from pathlib import Path

from momukbot.config import Settings
from momukbot.storage.quota import QuotaExceeded
from momukbot.search.naver import NaverSearchProvider, build_blog_evidence, score_blog_evidence


def settings(tmp_path: Path) -> Settings:
    return Settings(
        telegram_bot_token="",
        telegram_allowed_chat_ids=(),
        telegram_admin_user_ids=(),
        naver_client_id="client",
        naver_client_secret="secret",
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


def test_score_blog_evidence_penalizes_missing_area() -> None:
    score, signals, penalties = score_blog_evidence(
        title="충무로 해장 감자탕 후기",
        summary="국밥과 감자탕이 괜찮았습니다.",
        postdate="20260420",
        area="서면",
        topic="해장 국밥 감자탕",
        today=date(2026, 5, 1),
    )

    assert score < 10
    assert "area_missing:서면" in penalties
    assert "title_match:해장" in signals


def test_score_blog_evidence_matches_landmark_area_variants() -> None:
    examples = [
        (
            "전주 한옥마을",
            "한옥마을 비빔밥 맛집 방문 후기",
            "전주 여행 중 직접 다녀왔습니다.",
        ),
        (
            "해운대 해수욕장",
            "해운대 조개구이 맛집 방문 후기",
            "바닷가 근처에서 먹고 온 후기입니다.",
        ),
        (
            "인천 송도 센트럴파크",
            "송도 센트럴파크 근처 맛집 방문 후기",
            "직접 주문해서 먹고 왔습니다.",
        ),
    ]

    for area, title, summary in examples:
        _, _, penalties = score_blog_evidence(
            title=title,
            summary=summary,
            postdate="20260420",
            area=area,
            topic="맛집",
            today=date(2026, 5, 1),
        )

        assert f"area_missing:{area}" not in penalties, area


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


def test_build_context_adds_secondary_context_query_without_replacing_primary(tmp_path: Path) -> None:
    provider = NaverSearchProvider(settings(tmp_path))
    queries: list[tuple[str, str]] = []

    def fake_search(endpoint: str, query: str, display: int = 10, sort: str = "sim"):
        queries.append((endpoint, query))
        if endpoint == "blog" and "혼술" in query:
            return {
                "items": [
                    {
                        "title": "이태원 맛집 혼술 방문 후기",
                        "description": "혼자 방문해도 편했고 음식 후기가 좋았습니다.",
                        "postdate": "20260420",
                        "bloggername": "context",
                        "link": "https://blog.naver.com/context/post",
                    },
                ]
            }
        if endpoint == "blog":
            return {
                "items": [
                    {
                        "title": "이태원 맛집 방문 후기",
                        "description": "직접 다녀온 음식 후기입니다.",
                        "postdate": "20260421",
                        "bloggername": "primary",
                        "link": "https://blog.naver.com/primary/post",
                    },
                ]
            }
        return {"items": []}

    provider.search = fake_search  # type: ignore[method-assign]

    context = provider.build_context("이태원", "", count=30, context_hint="혼술")

    assert queries[0] == ("blog", "이태원 맛집 후기")
    assert ("blog", "이태원 맛집 혼술 후기") in queries
    assert "Primary Naver Blog Search results" in context.text
    assert "Secondary context Naver Blog Search results" in context.text
    assert "blogger=primary" in context.text
    assert "blogger=context" in context.text


def test_build_context_instructs_agent_to_search_naver_blog_when_quota_blocked(tmp_path: Path) -> None:
    provider = NaverSearchProvider(settings(tmp_path))

    def fake_search(endpoint: str, query: str, display: int = 10, sort: str = "sim"):
        raise QuotaExceeded("blocked")

    provider.search = fake_search  # type: ignore[method-assign]

    context = provider.build_context("이태원", "", count=30)

    assert context.quota_blocked is True
    assert "Naver API quota is blocked" in context.text
    assert "site:blog.naver.com 이태원 맛집 후기" in context.text
    assert "Optional user hint: 혼술바" not in context.text
    assert "Do not use Tistory" in context.text
