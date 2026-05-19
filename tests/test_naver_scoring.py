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
        if endpoint == "local":
            return {
                "items": [
                    {
                        "title": "서면국밥",
                        "category": "한식>국밥",
                        "roadAddress": "부산 부산진구 서면로",
                        "link": "https://map.naver.com/seomyeon-gukbap",
                    }
                ]
            }
        if endpoint == "blog":
            return {
                "items": [
                    {
                        "title": "오래된 서면국밥 BEST",
                        "description": "서면국밥 광고 리스트",
                        "postdate": "20220101",
                        "bloggername": "old",
                        "link": "https://blog.naver.com/old/post",
                    },
                    {
                        "title": "서면국밥 방문 후기",
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
    assert "blog_url=https://blog.naver.com/recent/post" in context
    assert "blog_url=https://blog.naver.com/old/post" not in context


def test_build_context_adds_secondary_context_query_without_replacing_primary(tmp_path: Path) -> None:
    provider = NaverSearchProvider(settings(tmp_path))
    queries: list[tuple[str, str]] = []

    def fake_search(endpoint: str, query: str, display: int = 10, sort: str = "sim"):
        queries.append((endpoint, query))
        if endpoint == "local":
            return {
                "items": [
                    {
                        "title": "이태원혼술집",
                        "category": "음식점>주점",
                        "roadAddress": "서울 용산구 이태원로",
                        "link": "https://map.naver.com/itaewon-bar",
                    }
                ]
            }
        if endpoint == "blog" and "혼술" in query:
            return {
                "items": [
                    {
                        "title": "이태원혼술집 혼술 방문 후기",
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
                        "title": "이태원혼술집 방문 후기",
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

    assert queries[0] == ("local", "이태원 맛집")
    assert ("blog", "이태원 맛집 혼술 후기") in queries
    assert "Verified Naver Local + Naver Blog evidence matches" in context.text
    assert "blog_url=https://blog.naver.com/context/post" in context.text
    assert context.evidence_available is True


def test_build_context_uses_local_candidates_only_when_blog_evidence_matches(
    tmp_path: Path,
) -> None:
    provider = NaverSearchProvider(settings(tmp_path))

    def fake_search(endpoint: str, query: str, display: int = 10, sort: str = "sim"):
        if endpoint == "local":
            return {
                "items": [
                    {
                        "title": "스타벅스 목동역점",
                        "category": "카페,디저트",
                        "roadAddress": "서울 양천구",
                        "link": "https://map.naver.com/starbucks",
                    },
                    {
                        "title": "목동한식당",
                        "category": "한식",
                        "roadAddress": "서울 양천구 목동",
                        "link": "https://map.naver.com/korean",
                    },
                    {
                        "title": "목동스시",
                        "category": "일식",
                        "roadAddress": "서울 양천구 목동",
                        "link": "https://map.naver.com/sushi",
                    },
                ]
            }
        if endpoint == "blog":
            return {
                "items": [
                    {
                        "title": "목동한식당 방문 후기",
                        "description": "목동한식당에서 직접 먹고 온 후기입니다.",
                        "postdate": "20260420",
                        "bloggername": "food",
                        "link": "https://blog.naver.com/food/korean",
                    },
                    {
                        "title": "목동스시 방문 후기",
                        "description": "목동스시에서 주문한 초밥 후기입니다.",
                        "postdate": "20260421",
                        "bloggername": "sushi",
                        "link": "https://blog.naver.com/food/sushi",
                    },
                ]
            }
        return {"items": []}

    provider.search = fake_search  # type: ignore[method-assign]

    context = provider.build_context("목동역", "맛집", count=2)

    assert "Deterministic Naver local candidate roster" not in context.text
    assert "Verified Naver Local + Naver Blog evidence matches" in context.text
    assert [candidate.name for candidate in context.candidates] == ["목동한식당", "목동스시"]
    assert "blog_url=https://blog.naver.com/food/korean" in context.text
    assert "스타벅스" not in context.text
    assert context.evidence_available is True


def test_build_context_keeps_only_best_blog_evidence_and_truncates_summary(tmp_path: Path) -> None:
    provider = NaverSearchProvider(settings(tmp_path))
    long_summary = "목동한식당에서 직접 먹고 온 후기입니다. " + ("추천 메뉴가 좋았습니다. " * 20)

    def fake_search(endpoint: str, query: str, display: int = 10, sort: str = "sim"):
        if endpoint == "local":
            return {
                "items": [
                    {
                        "title": "목동한식당",
                        "category": "한식",
                        "roadAddress": "서울 양천구 목동",
                        "link": "https://map.naver.com/korean",
                    }
                ]
            }
        if endpoint == "blog":
            return {
                "items": [
                    {
                        "title": "목동한식당 방문 후기",
                        "description": long_summary,
                        "postdate": "20260420",
                        "bloggername": "best",
                        "link": "https://blog.naver.com/food/best",
                    },
                    {
                        "title": "목동한식당 두 번째 후기",
                        "description": "목동한식당에서 먹고 온 다른 후기입니다.",
                        "postdate": "20260419",
                        "bloggername": "second",
                        "link": "https://blog.naver.com/food/second",
                    },
                ]
            }
        return {"items": []}

    provider.search = fake_search  # type: ignore[method-assign]

    context = provider.build_context("목동역", "맛집", count=1)

    assert "blog_url=https://blog.naver.com/food/best" in context.text
    assert "blog_url=https://blog.naver.com/food/second" not in context.text
    assert context.text.count("추천 메뉴가 좋았습니다.") < 8


def test_build_context_allows_local_verified_cafe_for_explicit_coffee_request(tmp_path: Path) -> None:
    provider = NaverSearchProvider(settings(tmp_path))

    def fake_search(endpoint: str, query: str, display: int = 10, sort: str = "sim"):
        if endpoint == "local":
            return {
                "items": [
                    {
                        "title": "스타벅스 목동역점",
                        "category": "카페,디저트",
                        "roadAddress": "서울 양천구",
                        "link": "https://map.naver.com/starbucks",
                    }
                ]
            }
        if endpoint == "blog":
            return {
                "items": [
                    {
                        "title": "스타벅스 목동역점 커피 후기",
                        "description": "스타벅스 목동역점 방문 후기입니다.",
                        "postdate": "20260420",
                        "bloggername": "coffee",
                        "link": "https://blog.naver.com/coffee/starbucks",
                    }
                ]
            }
        return {"items": []}

    provider.search = fake_search  # type: ignore[method-assign]

    context = provider.build_context("목동역", "커피", count=1)

    assert len(context.candidates) == 1
    assert context.candidates[0].name == "스타벅스 목동역점"
    assert context.candidates[0].category == "카페"
    assert "blog_url=https://blog.naver.com/coffee/starbucks" in context.text
    assert context.evidence_available is True


def test_build_context_rejects_local_only_candidates_without_blog_match(tmp_path: Path) -> None:
    provider = NaverSearchProvider(settings(tmp_path))

    def fake_search(endpoint: str, query: str, display: int = 10, sort: str = "sim"):
        if endpoint == "local":
            return {
                "items": [
                    {
                        "title": "목동한식당",
                        "category": "한식",
                        "roadAddress": "서울 양천구 목동",
                        "link": "https://map.naver.com/korean",
                    }
                ]
            }
        if endpoint == "blog":
            return {
                "items": [
                    {
                        "title": "다른가게 방문 후기",
                        "description": "다른가게에서 먹고 온 후기입니다.",
                        "postdate": "20260420",
                        "bloggername": "other",
                        "link": "https://blog.naver.com/other/post",
                    }
                ]
            }
        return {"items": []}

    provider.search = fake_search  # type: ignore[method-assign]

    context = provider.build_context("목동역", "맛집", count=1)

    assert context.candidates == []
    assert "목동한식당" not in context.text
    assert "다른가게" not in context.text
    assert context.evidence_available is False


def test_build_context_rejects_short_name_substring_false_positive(tmp_path: Path) -> None:
    provider = NaverSearchProvider(settings(tmp_path))

    def fake_search(endpoint: str, query: str, display: int = 10, sort: str = "sim"):
        if endpoint == "local":
            return {
                "items": [
                    {
                        "title": "하이",
                        "category": "술집>요리주점",
                        "roadAddress": "서울 양천구 목동",
                        "link": "https://map.naver.com/hi",
                    }
                ]
            }
        if endpoint == "blog":
            return {
                "items": [
                    {
                        "title": "목동역 맛집 오목교곱창 후기",
                        "description": "곱창과 하이볼까지 맛있게 먹고 왔습니다.",
                        "postdate": "20260420",
                        "bloggername": "food",
                        "link": "https://blog.naver.com/food/gopchang",
                    },
                    {
                        "title": "목동역 투룸 계약 후기 하이부동산",
                        "description": "하이부동산공인중개사사무소 매물 후기입니다.",
                        "postdate": "20260421",
                        "bloggername": "realty",
                        "link": "https://blog.naver.com/realty/room",
                    },
                ]
            }
        return {"items": []}

    provider.search = fake_search  # type: ignore[method-assign]

    context = provider.build_context("목동역", "맛집", count=1)

    assert context.candidates == []
    assert "place=하이" not in context.text
    assert context.evidence_available is False


def test_build_context_accepts_short_name_when_it_appears_as_standalone_token(tmp_path: Path) -> None:
    provider = NaverSearchProvider(settings(tmp_path))

    def fake_search(endpoint: str, query: str, display: int = 10, sort: str = "sim"):
        if endpoint == "local":
            return {
                "items": [
                    {
                        "title": "하이",
                        "category": "술집>요리주점",
                        "roadAddress": "서울 양천구 목동",
                        "link": "https://map.naver.com/hi",
                    }
                ]
            }
        if endpoint == "blog":
            return {
                "items": [
                    {
                        "title": "목동역 요리주점 하이 방문 후기",
                        "description": "하이에서 직접 주문해서 먹고 왔습니다.",
                        "postdate": "20260420",
                        "bloggername": "food",
                        "link": "https://blog.naver.com/food/hi",
                    }
                ]
            }
        return {"items": []}

    provider.search = fake_search  # type: ignore[method-assign]

    context = provider.build_context("목동역", "맛집", count=1)

    assert [candidate.name for candidate in context.candidates] == ["하이"]
    assert "blog_url=https://blog.naver.com/food/hi" in context.text
    assert context.evidence_available is True


def test_build_context_runs_targeted_blog_search_when_broad_blog_does_not_match(tmp_path: Path) -> None:
    provider = NaverSearchProvider(settings(tmp_path))
    queries: list[tuple[str, str]] = []

    def fake_search(endpoint: str, query: str, display: int = 10, sort: str = "sim"):
        queries.append((endpoint, query))
        if endpoint == "local":
            return {
                "items": [
                    {
                        "title": "목동한식당",
                        "category": "한식",
                        "roadAddress": "서울 양천구 목동",
                        "link": "https://map.naver.com/korean",
                    }
                ]
            }
        if endpoint == "blog" and query == "목동역 목동한식당 후기":
            return {
                "items": [
                    {
                        "title": "목동한식당 방문 후기",
                        "description": "목동한식당에서 직접 먹고 왔습니다.",
                        "postdate": "20260420",
                        "bloggername": "targeted",
                        "link": "https://blog.naver.com/targeted/korean",
                    }
                ]
            }
        if endpoint == "blog":
            return {
                "items": [
                    {
                        "title": "목동역 다른가게 방문 후기",
                        "description": "다른가게에서 먹고 온 후기입니다.",
                        "postdate": "20260420",
                        "bloggername": "broad",
                        "link": "https://blog.naver.com/broad/other",
                    }
                ]
            }
        return {"items": []}

    provider.search = fake_search  # type: ignore[method-assign]

    context = provider.build_context("목동역", "맛집", count=1)

    assert ("blog", "목동역 맛집 후기") in queries
    assert ("blog", "목동역 목동한식당 후기") in queries
    assert [candidate.name for candidate in context.candidates] == ["목동한식당"]
    assert "blog_url=https://blog.naver.com/targeted/korean" in context.text
    assert "다른가게" not in context.text
    assert context.evidence_available is True


def test_build_context_limits_targeted_blog_searches(tmp_path: Path) -> None:
    provider = NaverSearchProvider(settings(tmp_path))
    queries: list[tuple[str, str]] = []
    local_calls = 0

    def fake_search(endpoint: str, query: str, display: int = 10, sort: str = "sim"):
        nonlocal local_calls
        queries.append((endpoint, query))
        if endpoint == "local":
            local_calls += 1
            start = (local_calls - 1) * 5
            return {
                "items": [
                    {
                        "title": f"목동식당{start + index}",
                        "category": "한식",
                        "roadAddress": "서울 양천구 목동",
                        "link": f"https://map.naver.com/korean/{start + index}",
                    }
                    for index in range(1, 6)
                ]
            }
        if endpoint == "blog":
            return {"items": []}
        return {"items": []}

    provider.search = fake_search  # type: ignore[method-assign]

    provider.build_context("목동역", "맛집", count=30)

    targeted_queries = [
        query
        for endpoint, query in queries
        if endpoint == "blog" and query.startswith("목동역 목동식당")
    ]
    assert local_calls >= 12
    assert len(targeted_queries) == 30


def test_build_context_disables_agent_search_fallback_when_quota_blocked(tmp_path: Path) -> None:
    provider = NaverSearchProvider(settings(tmp_path))

    def fake_search(endpoint: str, query: str, display: int = 10, sort: str = "sim"):
        raise QuotaExceeded("blocked")

    provider.search = fake_search  # type: ignore[method-assign]

    context = provider.build_context("이태원", "", count=30)

    assert context.quota_blocked is True
    assert context.evidence_available is False
    assert "Naver API quota is blocked" in context.text
    assert "site:blog.naver.com 이태원 맛집 후기" in context.text
    assert "Optional user hint: 혼술바" not in context.text
    assert "Do not use Tistory" in context.text
    assert "Do not use your own web search capability" in context.text


def test_build_context_uses_local_queries_before_blog_queries(tmp_path: Path) -> None:
    provider = NaverSearchProvider(settings(tmp_path))
    queries: list[tuple[str, str]] = []

    def fake_search(endpoint: str, query: str, display: int = 10, sort: str = "sim"):
        queries.append((endpoint, query))
        if endpoint == "local":
            return {
                "items": [
                    {
                        "title": "서면국밥",
                        "category": "한식",
                        "roadAddress": "부산 부산진구 서면로",
                        "link": "https://map.naver.com/gukbap",
                    }
                ]
            }
        return {"items": []}

    provider.search = fake_search  # type: ignore[method-assign]

    provider.build_context("서면", "국밥", count=30)

    assert queries[0] == ("local", "서면 국밥")
    local_queries = [query for endpoint, query in queries if endpoint == "local"]
    blog_queries = [query for endpoint, query in queries if endpoint == "blog"]
    assert "서면 국밥" in local_queries
    assert "서면 해장국" in local_queries
    assert "서면 국밥 맛집 후기" in blog_queries
