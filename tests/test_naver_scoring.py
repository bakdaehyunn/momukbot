from datetime import date
from pathlib import Path
from typing import Any

from momukbot.config import Settings
from momukbot.core.models import SearchCandidate
from momukbot.search.candidates import (
    _allows_cafe_candidates,
    _candidate_key,
    _is_excluded_general_candidate,
    _local_candidate_queries,
    _local_candidate_target_count,
)
from momukbot.search.hybrid import HybridSearchProvider
from momukbot.search.kakao import candidate_from_kakao_document
from momukbot.storage.quota import QuotaExceeded
from momukbot.search.naver import (
    NaverBlogEvidenceProvider,
    _format_verified_matches,
    _match_local_candidates_to_blog,
    build_blog_evidence,
    score_blog_evidence,
)


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
        kakao_rest_api_key="kakao-key",
    )


class _HybridProviderFixture:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def search(self, endpoint: str, query: str, display: int = 10, sort: str = "sim") -> dict[str, Any]:
        return {"items": []}

    def build_context(
        self,
        area: str,
        topic: str,
        count: int = 30,
        context_hint: str = "",
    ):
        return HybridSearchProvider(
            self.settings,
            kakao_provider=_KakaoProviderFixture(self),
            blog_provider=_BlogProviderFixture(self.settings, self),
        ).build_context(area, topic, count=count, context_hint=context_hint)


class _KakaoProviderFixture:
    configured = True

    def __init__(self, owner: _HybridProviderFixture) -> None:
        self.owner = owner

    def build_candidates(
        self,
        area: str,
        topic: str,
        count: int,
        context_hint: str = "",
        expanded: bool = False,
        initial_candidates: list[SearchCandidate] | None = None,
    ) -> list[SearchCandidate]:
        allow_cafe = _allows_cafe_candidates(topic, context_hint)
        candidates = list(initial_candidates or [])
        seen_candidates = {_candidate_key(candidate) for candidate in candidates}
        seen_queries = {candidate.query for candidate in candidates if candidate.query}
        queries = _local_candidate_queries(area, topic, count, context_hint, expanded=expanded)
        target_count = _local_candidate_target_count(count, expanded=expanded)
        max_queries = min(len(queries), max(1, (target_count + 4) // 5))
        for query in queries[:max_queries]:
            if query in seen_queries:
                continue
            seen_queries.add(query)
            kakao = self.owner.search("kakao", query, display=5, sort="distance")
            documents = kakao.get("documents") if isinstance(kakao, dict) else []
            if not isinstance(documents, list):
                continue
            for document in documents:
                if not isinstance(document, dict):
                    continue
                candidate = candidate_from_kakao_document(document, query)
                if candidate is None:
                    continue
                key = _candidate_key(candidate)
                if not key or key in seen_candidates:
                    continue
                if not allow_cafe and _is_excluded_general_candidate(candidate):
                    continue
                seen_candidates.add(key)
                candidates.append(candidate)
                if len(candidates) >= target_count:
                    return candidates
        return candidates


class _BlogProviderFixture(NaverBlogEvidenceProvider):
    def __init__(self, settings: Settings, owner: _HybridProviderFixture) -> None:
        super().__init__(settings)
        self.owner = owner

    def search(self, endpoint: str, query: str, display: int = 10, sort: str = "sim") -> dict[str, Any]:
        if endpoint != "blog":
            raise AssertionError(f"unexpected Naver endpoint: {endpoint}")
        return self.owner.search(endpoint, query, display=display, sort=sort)


def _test_kakao_url(value: str) -> str:
    return f"https://place.map.kakao.com/{abs(hash(value)) % 1000000 + 100000}"


def _candidate(name: str) -> SearchCandidate:
    return SearchCandidate(
        name=name,
        category="국밥",
        raw_category="음식점 > 한식 > 국밥",
        address="서울 양천구 목동",
        url=_test_kakao_url(name),
        source="kakao_local",
        query="목동역 국밥",
    )


def _blog(title: str, summary: str, postdate: str, blogger: str):
    return build_blog_evidence(
        {
            "title": title,
            "description": summary,
            "postdate": postdate,
            "bloggername": blogger,
            "link": f"https://blog.naver.com/{blogger}/{abs(hash((title, postdate))) % 100000}",
        },
        area="목동역",
        topic="국밥",
        today=date(2026, 6, 17),
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


def test_score_blog_evidence_boosts_unlimited_refill_when_requested() -> None:
    score, signals, penalties = score_blog_evidence(
        title="목동역 무한리필 샤브샤브 편편집 방문 후기",
        summary="월남쌈과 샐러드바를 무제한으로 먹을 수 있고 1인 가격도 적혀 있었습니다.",
        postdate="20260420",
        area="목동역",
        topic="무한리필 샤브샤브 맛집",
        today=date(2026, 5, 1),
    )

    assert score >= 15
    assert "unlimited:무한리필" in signals
    assert "unlimited:무제한" in signals
    assert penalties == []


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


def test_match_scoring_prefers_multiple_exact_recent_matches_over_single_match() -> None:
    candidates = [_candidate("한번국밥"), _candidate("인기국밥")]
    evidence = [
        _blog("목동역 한번국밥 방문 후기", "한번국밥에서 먹고 왔습니다.", "20260601", "solo"),
        _blog("목동역 인기국밥 방문 후기", "인기국밥에서 먹고 왔습니다.", "20260602", "a"),
        _blog("목동역 인기국밥 내돈내산", "인기국밥 재방문 후기입니다.", "20260525", "b"),
        _blog("목동역 인기국밥 웨이팅 후기", "인기국밥 주문 후기입니다.", "20260520", "c"),
    ]

    matches = _match_local_candidates_to_blog(candidates, evidence, count=2)

    assert [match.candidate.name for match in matches] == ["인기국밥", "한번국밥"]
    assert matches[0].matched_post_count == 3
    assert matches[0].recent_post_count == 3
    assert matches[0].unique_blogger_count == 3
    assert matches[0].aggregate_score > matches[1].aggregate_score


def test_match_scoring_prefers_recent_posts_over_stale_posts() -> None:
    candidates = [_candidate("옛날국밥"), _candidate("요즘국밥")]
    evidence = [
        _blog("목동역 옛날국밥 방문 후기", "옛날국밥에서 먹고 왔습니다.", "20210101", "old"),
        _blog("목동역 요즘국밥 방문 후기", "요즘국밥에서 먹고 왔습니다.", "20260601", "new"),
    ]

    matches = _match_local_candidates_to_blog(candidates, evidence, count=2)

    assert [match.candidate.name for match in matches] == ["요즘국밥", "옛날국밥"]
    assert matches[0].recent_post_count == 1
    assert matches[1].stale_post_count == 1


def test_match_scoring_prefers_unique_bloggers_over_duplicate_blogger_posts() -> None:
    candidates = [_candidate("중복국밥"), _candidate("다양국밥")]
    evidence = [
        _blog("목동역 중복국밥 방문 후기", "중복국밥에서 먹고 왔습니다.", "20260601", "same"),
        _blog("목동역 중복국밥 재방문 후기", "중복국밥 내돈내산 후기입니다.", "20260530", "same"),
        _blog("목동역 다양국밥 방문 후기", "다양국밥에서 먹고 왔습니다.", "20260601", "a"),
        _blog("목동역 다양국밥 재방문 후기", "다양국밥 내돈내산 후기입니다.", "20260530", "b"),
    ]

    matches = _match_local_candidates_to_blog(candidates, evidence, count=2)

    assert [match.candidate.name for match in matches] == ["다양국밥", "중복국밥"]
    assert matches[0].unique_blogger_count == 2
    assert matches[1].unique_blogger_count == 1


def test_match_scoring_weights_title_exact_match_above_summary_only_match() -> None:
    candidates = [_candidate("요약국밥"), _candidate("제목국밥")]
    evidence = [
        _blog("목동역 국밥 방문 후기", "요약국밥에서 먹고 왔습니다.", "20260601", "summary"),
        _blog("목동역 제목국밥 방문 후기", "직접 먹고 왔습니다.", "20260601", "title"),
    ]

    matches = _match_local_candidates_to_blog(candidates, evidence, count=2)

    assert [match.candidate.name for match in matches] == ["제목국밥", "요약국밥"]
    assert matches[0].title_match_count == 1
    assert matches[1].summary_match_count == 1


def test_match_scoring_penalizes_ad_like_snippets() -> None:
    candidates = [_candidate("광고국밥"), _candidate("방문국밥")]
    evidence = [
        _blog("목동역 광고국밥 방문 후기", "광고국밥 원고료를 제공받아 작성했습니다.", "20260601", "ad"),
        _blog("목동역 방문국밥 방문 후기", "방문국밥에서 직접 먹고 왔습니다.", "20260601", "real"),
    ]

    matches = _match_local_candidates_to_blog(candidates, evidence, count=2)

    assert [match.candidate.name for match in matches] == ["방문국밥", "광고국밥"]
    assert matches[1].ad_like_count == 1


def test_verified_context_exposes_api_snippet_metrics_not_full_body_claims() -> None:
    candidate = _candidate("인기국밥")
    evidence = [
        _blog("목동역 인기국밥 방문 후기", "인기국밥에서 먹고 왔습니다.", "20260601", "a"),
        _blog("목동역 인기국밥 내돈내산", "인기국밥 재방문 후기입니다.", "20260525", "b"),
    ]
    match = _match_local_candidates_to_blog([candidate], evidence, count=1)[0]

    context = "\n".join(_format_verified_matches([match]))

    assert "matched_post_count=2" in context
    assert "recent_post_count=2" in context
    assert "unique_blogger_count=2" in context
    assert "title_exact_match_count=2" in context
    assert "blog_summary=" in context
    assert "full_body" not in context
    assert "crawler" not in context.lower()


def test_build_context_orders_blog_evidence_by_score(tmp_path: Path) -> None:
    provider = _HybridProviderFixture(settings(tmp_path))

    def fake_search(endpoint: str, query: str, display: int = 10, sort: str = "sim"):
        if endpoint == "kakao":
            return {
                "documents": [
                    {
                        "place_name": "서면국밥",
                        "category_name": "한식>국밥",
                        "road_address_name": "부산 부산진구 서면로",
                        "place_url": "https://place.map.kakao.com/123456",
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
    provider = _HybridProviderFixture(settings(tmp_path))
    queries: list[tuple[str, str]] = []

    def fake_search(endpoint: str, query: str, display: int = 10, sort: str = "sim"):
        queries.append((endpoint, query))
        if endpoint == "kakao":
            return {
                "documents": [
                    {
                        "place_name": "이태원혼술집",
                        "category_name": "음식점>주점",
                        "road_address_name": "서울 용산구 이태원로",
                        "place_url": "https://place.map.kakao.com/123456",
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

    assert queries[0] == ("kakao", "이태원 맛집")
    assert ("blog", "이태원 맛집 혼술 후기") in queries
    assert "Verified Kakao Local + Naver Blog evidence matches" in context.text
    assert "blog_url=https://blog.naver.com/context/post" in context.text
    assert context.evidence_available is True


def test_build_context_uses_kakao_candidates_only_when_blog_evidence_matches(
    tmp_path: Path,
) -> None:
    provider = _HybridProviderFixture(settings(tmp_path))

    def fake_search(endpoint: str, query: str, display: int = 10, sort: str = "sim"):
        if endpoint == "kakao":
            return {
                "documents": [
                    {
                        "place_name": "스타벅스 목동역점",
                        "category_name": "카페,디저트",
                        "road_address_name": "서울 양천구",
                        "place_url": "https://place.map.kakao.com/123456",
                    },
                    {
                        "place_name": "목동한식당",
                        "category_name": "한식",
                        "road_address_name": "서울 양천구 목동",
                        "place_url": "https://place.map.kakao.com/123456",
                    },
                    {
                        "place_name": "목동스시",
                        "category_name": "일식",
                        "road_address_name": "서울 양천구 목동",
                        "place_url": "https://place.map.kakao.com/123456",
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

    assert "candidate roster" not in context.text
    assert "Verified Kakao Local + Naver Blog evidence matches" in context.text
    assert [candidate.name for candidate in context.candidates] == ["목동한식당", "목동스시"]
    assert "blog_url=https://blog.naver.com/food/korean" in context.text
    assert "스타벅스" not in context.text
    assert context.evidence_available is True


def test_build_context_keeps_top_supporting_blog_evidence_and_truncates_summary(tmp_path: Path) -> None:
    provider = _HybridProviderFixture(settings(tmp_path))
    long_summary = "목동한식당에서 직접 먹고 온 후기입니다. " + ("추천 메뉴가 좋았습니다. " * 20)

    def fake_search(endpoint: str, query: str, display: int = 10, sort: str = "sim"):
        if endpoint == "kakao":
            return {
                "documents": [
                    {
                        "place_name": "목동한식당",
                        "category_name": "한식",
                        "road_address_name": "서울 양천구 목동",
                        "place_url": "https://place.map.kakao.com/123456",
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
    assert "blog_url=https://blog.naver.com/food/second" in context.text
    assert context.text.count("추천 메뉴가 좋았습니다.") < 8


def test_build_context_allows_kakao_verified_cafe_for_explicit_coffee_request(tmp_path: Path) -> None:
    provider = _HybridProviderFixture(settings(tmp_path))

    def fake_search(endpoint: str, query: str, display: int = 10, sort: str = "sim"):
        if endpoint == "kakao":
            return {
                "documents": [
                    {
                        "place_name": "스타벅스 목동역점",
                        "category_name": "카페,디저트",
                        "road_address_name": "서울 양천구",
                        "place_url": "https://place.map.kakao.com/123456",
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


def test_build_context_rejects_kakao_only_candidates_without_blog_match(tmp_path: Path) -> None:
    provider = _HybridProviderFixture(settings(tmp_path))

    def fake_search(endpoint: str, query: str, display: int = 10, sort: str = "sim"):
        if endpoint == "kakao":
            return {
                "documents": [
                    {
                        "place_name": "목동한식당",
                        "category_name": "한식",
                        "road_address_name": "서울 양천구 목동",
                        "place_url": "https://place.map.kakao.com/123456",
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
    provider = _HybridProviderFixture(settings(tmp_path))

    def fake_search(endpoint: str, query: str, display: int = 10, sort: str = "sim"):
        if endpoint == "kakao":
            return {
                "documents": [
                    {
                        "place_name": "하이",
                        "category_name": "술집>요리주점",
                        "road_address_name": "서울 양천구 목동",
                        "place_url": "https://place.map.kakao.com/123456",
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
    provider = _HybridProviderFixture(settings(tmp_path))

    def fake_search(endpoint: str, query: str, display: int = 10, sort: str = "sim"):
        if endpoint == "kakao":
            return {
                "documents": [
                    {
                        "place_name": "하이",
                        "category_name": "술집>요리주점",
                        "road_address_name": "서울 양천구 목동",
                        "place_url": "https://place.map.kakao.com/123456",
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
    provider = _HybridProviderFixture(settings(tmp_path))
    queries: list[tuple[str, str]] = []

    def fake_search(endpoint: str, query: str, display: int = 10, sort: str = "sim"):
        queries.append((endpoint, query))
        if endpoint == "kakao":
            return {
                "documents": [
                    {
                        "place_name": "목동한식당",
                        "category_name": "한식",
                        "road_address_name": "서울 양천구 목동",
                        "place_url": "https://place.map.kakao.com/123456",
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
    provider = _HybridProviderFixture(settings(tmp_path))
    queries: list[tuple[str, str]] = []
    kakao_calls = 0

    def fake_search(endpoint: str, query: str, display: int = 10, sort: str = "sim"):
        nonlocal kakao_calls
        queries.append((endpoint, query))
        if endpoint == "kakao":
            kakao_calls += 1
            start = (kakao_calls - 1) * 5
            return {
                "documents": [
                    {
                        "place_name": f"목동식당{start + index}",
                        "category_name": "한식",
                        "road_address_name": "서울 양천구 목동",
                        "place_url": f"https://place.map.kakao.com/123456",
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
    assert kakao_calls >= 12
    assert len(targeted_queries) == 30


def test_build_context_runs_second_wave_when_verified_candidates_are_underfilled(tmp_path: Path) -> None:
    provider = _HybridProviderFixture(settings(tmp_path))
    queries: list[tuple[str, str]] = []

    def fake_search(endpoint: str, query: str, display: int = 10, sort: str = "sim"):
        queries.append((endpoint, query))
        if endpoint == "kakao":
            title = "목동백반" if query == "목동역 백반" else f"{query.replace(' ', '')}집"
            return {
                "documents": [
                    {
                        "place_name": title,
                        "category_name": "한식",
                        "road_address_name": "서울 양천구 목동",
                        "place_url": f"https://place.map.kakao.com/123456",
                    }
                ]
            }
        if endpoint == "blog" and query == "목동역 밥집 후기":
            return {
                "items": [
                    {
                        "title": "목동역 백반 목동백반 내돈내산 방문 후기",
                        "description": "목동백반에서 점심 백반을 먹고 온 후기입니다.",
                        "bloggername": "food",
                        "link": "https://blog.naver.com/food/baekban",
                        "postdate": "20260501",
                    }
                ]
            }
        return {"items": []}

    provider.search = fake_search  # type: ignore[method-assign]

    context = provider.build_context("목동역", "맛집", count=30)

    assert ("kakao", "목동역 백반") in queries
    assert ("blog", "목동역 밥집 후기") in queries
    assert [candidate.name for candidate in context.candidates] == ["목동백반"]
    assert "blog_url=https://blog.naver.com/food/baekban" in context.text
    assert context.evidence_available is True


def test_build_context_disables_agent_search_fallback_when_quota_blocked(tmp_path: Path) -> None:
    provider = _HybridProviderFixture(settings(tmp_path))

    def fake_search(endpoint: str, query: str, display: int = 10, sort: str = "sim"):
        if endpoint == "kakao":
            return {
                "documents": [
                    {
                        "place_name": "이태원밥집",
                        "category_name": "한식",
                        "road_address_name": "서울 용산구 이태원로",
                        "place_url": "https://place.map.kakao.com/123456",
                    }
                ]
            }
        raise QuotaExceeded("blocked")

    provider.search = fake_search  # type: ignore[method-assign]

    context = provider.build_context("이태원", "", count=30)

    assert context.quota_blocked is True
    assert context.evidence_available is False
    assert "Naver Blog API quota is blocked" in context.text
    assert "site:blog.naver.com" not in context.text
    assert "Do not use your own web search capability" not in context.text


def test_build_context_uses_kakao_queries_before_blog_queries(tmp_path: Path) -> None:
    provider = _HybridProviderFixture(settings(tmp_path))
    queries: list[tuple[str, str]] = []

    def fake_search(endpoint: str, query: str, display: int = 10, sort: str = "sim"):
        queries.append((endpoint, query))
        if endpoint == "kakao":
            return {
                "documents": [
                    {
                        "place_name": "서면국밥",
                        "category_name": "한식",
                        "road_address_name": "부산 부산진구 서면로",
                        "place_url": "https://place.map.kakao.com/123456",
                    }
                ]
            }
        return {"items": []}

    provider.search = fake_search  # type: ignore[method-assign]

    provider.build_context("서면", "국밥", count=30)

    assert queries[0] == ("kakao", "서면 국밥")
    kakao_queries = [query for endpoint, query in queries if endpoint == "kakao"]
    blog_queries = [query for endpoint, query in queries if endpoint == "blog"]
    assert kakao_queries[:8] == [
        "서면 국밥",
        "서면 국밥 맛집",
        "서면 맛집 국밥",
        "서면 순대국",
        "서면 순댓국",
        "서면 순대국밥",
        "서면 돼지국밥",
        "서면 해장국",
    ]
    assert "서면 뼈해장국" in kakao_queries
    assert "서면 설렁탕" in kakao_queries
    assert "서면 곰탕" in kakao_queries
    assert kakao_queries.index("서면 돼지국밥") < kakao_queries.index("서면 감자탕")
    assert "서면 국밥 맛집 후기" in blog_queries
