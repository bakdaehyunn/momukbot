from pathlib import Path
from typing import Any

from momukbot.config import Settings
from momukbot.core.models import SearchCandidate
from momukbot.search.hybrid import HybridSearchProvider
from momukbot.search.kakao import (
    KakaoLocalCandidateProvider,
    candidate_from_kakao_document,
    kakao_candidate_queries,
    kakao_category_group_code,
)
from momukbot.search.naver import NaverBlogEvidenceProvider


def settings(tmp_path: Path) -> Settings:
    return Settings(
        telegram_bot_token="",
        telegram_allowed_chat_ids=(),
        telegram_admin_user_ids=(),
        naver_client_id="client",
        naver_client_secret="secret",
        naver_daily_soft_limit=100,
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


def test_candidate_from_kakao_document_maps_search_candidate_fields() -> None:
    candidate = candidate_from_kakao_document(
        {
            "place_name": "목동한식당",
            "category_name": "음식점 > 한식",
            "road_address_name": "서울 양천구 목동로 1",
            "address_name": "서울 양천구 목동",
            "place_url": "https://place.map.kakao.com/123456",
            "id": "123456",
            "phone": "02-123-4567",
            "category_group_code": "FD6",
            "category_group_name": "음식점",
            "x": "126.1",
            "y": "37.1",
            "distance": "35",
        },
        query="목동역 맛집",
        selected_region="서울 양천구 목동",
        region_candidates=("서울 양천구 목동",),
    )

    assert candidate is not None
    assert candidate.name == "목동한식당"
    assert candidate.raw_category == "음식점 > 한식"
    assert candidate.category == "한식"
    assert candidate.address == "서울 양천구 목동로 1"
    assert candidate.url == "https://place.map.kakao.com/123456"
    assert candidate.source == "kakao_local"
    assert candidate.query == "목동역 맛집"
    assert candidate.place_id == "123456"
    assert candidate.phone == "02-123-4567"
    assert candidate.category_group_code == "FD6"
    assert candidate.category_group_name == "음식점"
    assert candidate.x == "126.1"
    assert candidate.y == "37.1"
    assert candidate.distance == "35"
    assert candidate.selected_region == "서울 양천구 목동"
    assert candidate.region_candidates == ("서울 양천구 목동",)


def test_kakao_provider_excludes_candidates_without_place_url(tmp_path: Path) -> None:
    provider = FakeKakaoProvider(settings(tmp_path))

    candidates = provider.build_candidates("목동역", "맛집", count=2)

    assert [candidate.name for candidate in candidates] == ["목동한식당"]
    assert candidates[0].url == "https://place.map.kakao.com/123456"
    assert provider.calls
    assert {call["category_group_code"] for call in provider.calls} == {"FD6"}


def test_kakao_provider_uses_fd6_for_meal_requests(tmp_path: Path) -> None:
    provider = RecordingKakaoProvider(settings(tmp_path))

    provider.build_candidates("서면", "해장 국밥", count=30)

    assert provider.calls
    assert {call["category_group_code"] for call in provider.calls} == {"FD6"}
    assert provider.calls[0]["query"] == "부산 서면 돼지국밥"
    assert "서면 해장 국밥" not in [call["query"] for call in provider.calls[:4]]


def test_kakao_provider_uses_ce7_for_explicit_cafe_requests(tmp_path: Path) -> None:
    provider = RecordingKakaoProvider(settings(tmp_path))

    provider.build_candidates("서면", "카페", count=2)

    assert provider.calls
    assert {call["category_group_code"] for call in provider.calls} == {"CE7"}


def test_kakao_candidate_queries_prioritize_safer_region_qualified_gukbap_terms() -> None:
    queries = kakao_candidate_queries("서면", "해장 국밥", count=30)

    assert queries[:4] == ["부산 서면 돼지국밥", "부산 서면 순대국밥", "부산 서면 해장국", "부산 서면 국밥"]
    assert "서면 해장 국밥" in queries


def test_kakao_provider_rejects_wrong_selected_region(tmp_path: Path) -> None:
    provider = WrongRegionKakaoProvider(settings(tmp_path))

    candidates = provider.build_candidates("서면", "해장 국밥", count=2)

    assert candidates == []
    assert provider.calls
    assert {call["category_group_code"] for call in provider.calls} == {"FD6"}


def test_kakao_provider_accepts_matching_selected_region(tmp_path: Path) -> None:
    provider = MatchingRegionKakaoProvider(settings(tmp_path))

    candidates = provider.build_candidates("서면", "해장 국밥", count=2)

    assert [candidate.name for candidate in candidates] == ["송정삼대국밥"]
    assert candidates[0].selected_region == "부산 부산진구 서면"
    assert candidates[0].region_candidates == ("부산 부산진구", "서면")


def test_kakao_category_group_code_defaults_to_food_unless_cafe_is_explicit() -> None:
    assert kakao_category_group_code("맛집") == "FD6"
    assert kakao_category_group_code("해장 국밥") == "FD6"
    assert kakao_category_group_code("카페") == "CE7"


def test_hybrid_provider_recommends_only_kakao_candidates_with_naver_blog_match(tmp_path: Path) -> None:
    provider = HybridSearchProvider(
        settings(tmp_path),
        kakao_provider=StaticKakaoCandidates(),
        blog_provider=FakeNaverBlogProvider(settings(tmp_path)),
    )

    context = provider.build_context("목동역", "맛집", count=2)

    assert context.evidence_available is True
    assert context.used_provider == "kakao_local+naver_blog"
    assert [candidate.name for candidate in context.candidates] == ["목동한식당"]
    assert "Verified Kakao Local + Naver Blog evidence matches" in context.text
    assert "blog_url=https://blog.naver.com/food/korean" in context.text
    assert context.stats["kakao_candidate_count"] == 2
    assert context.stats["naver_blog_evidence_count"] == 1
    assert context.stats["matched_candidate_count"] == 1


def test_hybrid_provider_does_not_call_naver_local(tmp_path: Path) -> None:
    blog = FakeNaverBlogProvider(settings(tmp_path))
    provider = HybridSearchProvider(
        settings(tmp_path),
        kakao_provider=StaticKakaoCandidates(),
        blog_provider=blog,
    )

    provider.build_context("목동역", "맛집", count=2)

    assert blog.endpoints
    assert set(blog.endpoints) == {"blog"}


def test_hybrid_provider_fails_closed_when_blog_evidence_does_not_match(tmp_path: Path) -> None:
    provider = HybridSearchProvider(
        settings(tmp_path),
        kakao_provider=StaticKakaoCandidates(),
        blog_provider=NoMatchNaverBlogProvider(settings(tmp_path)),
    )

    context = provider.build_context("목동역", "맛집", count=2)

    assert context.evidence_available is False
    assert context.candidates == []
    assert "No Kakao Local candidates had matching Naver Blog evidence" in context.text


class FakeKakaoProvider(KakaoLocalCandidateProvider):
    def __init__(self, settings: Settings) -> None:
        super().__init__(settings)
        self.calls: list[dict[str, Any]] = []

    def search_keyword(
        self,
        query: str,
        size: int = 15,
        page: int = 1,
        category_group_code: str = "",
    ) -> dict[str, Any]:
        self.calls.append(
            {
                "query": query,
                "size": size,
                "page": page,
                "category_group_code": category_group_code,
            }
        )
        return {
            "meta": {
                "same_name": {
                    "selected_region": "서울 양천구 목동",
                    "region": ["서울 양천구 목동"],
                }
            },
            "documents": [
                {
                    "place_name": "목동한식당",
                    "category_name": "한식",
                    "road_address_name": "서울 양천구 목동로 1",
                    "place_url": "https://place.map.kakao.com/123456",
                    "category_group_code": "FD6",
                },
                {
                    "place_name": "링크없는밥집",
                    "category_name": "한식",
                    "road_address_name": "서울 양천구 목동로 2",
                    "place_url": "",
                },
            ]
        }


class RecordingKakaoProvider(KakaoLocalCandidateProvider):
    def __init__(self, settings: Settings) -> None:
        super().__init__(settings)
        self.calls: list[dict[str, Any]] = []

    def search_keyword(
        self,
        query: str,
        size: int = 15,
        page: int = 1,
        category_group_code: str = "",
    ) -> dict[str, Any]:
        self.calls.append(
            {
                "query": query,
                "size": size,
                "page": page,
                "category_group_code": category_group_code,
            }
        )
        return {"documents": []}


class WrongRegionKakaoProvider(RecordingKakaoProvider):
    def search_keyword(
        self,
        query: str,
        size: int = 15,
        page: int = 1,
        category_group_code: str = "",
    ) -> dict[str, Any]:
        self.calls.append(
            {
                "query": query,
                "size": size,
                "page": page,
                "category_group_code": category_group_code,
            }
        )
        return {
            "meta": {
                "same_name": {
                    "selected_region": "강원 춘천시 서면",
                    "region": ["강원 춘천시", "서면"],
                }
            },
            "documents": [
                {
                    "place_name": "달래해장 춘천애막골점",
                    "category_name": "음식점 > 한식",
                    "road_address_name": "강원 춘천시 서면 박사로 1",
                    "place_url": "https://place.map.kakao.com/555555",
                    "category_group_code": "FD6",
                }
            ],
        }


class MatchingRegionKakaoProvider(RecordingKakaoProvider):
    def search_keyword(
        self,
        query: str,
        size: int = 15,
        page: int = 1,
        category_group_code: str = "",
    ) -> dict[str, Any]:
        self.calls.append(
            {
                "query": query,
                "size": size,
                "page": page,
                "category_group_code": category_group_code,
            }
        )
        return {
            "meta": {
                "same_name": {
                    "selected_region": "부산 부산진구 서면",
                    "region": ["부산 부산진구", "서면"],
                }
            },
            "documents": [
                {
                    "place_name": "송정삼대국밥",
                    "category_name": "음식점 > 한식 > 국밥",
                    "road_address_name": "부산 부산진구 서면로 1",
                    "place_url": "https://place.map.kakao.com/777777",
                    "category_group_code": "FD6",
                }
            ],
        }


class StaticKakaoCandidates:
    configured = True

    def build_candidates(
        self,
        area: str,
        topic: str,
        count: int,
        context_hint: str = "",
        expanded: bool = False,
        initial_candidates: list[SearchCandidate] | None = None,
    ) -> list[SearchCandidate]:
        return [
            SearchCandidate(
                name="목동한식당",
                category="한식",
                raw_category="한식",
                address="서울 양천구 목동로 1",
                url="https://place.map.kakao.com/123456",
                source="kakao_local",
                query="목동역 맛집",
            ),
            SearchCandidate(
                name="목동스시",
                category="일식",
                raw_category="일식",
                address="서울 양천구 목동로 2",
                url="https://place.map.kakao.com/234567",
                source="kakao_local",
                query="목동역 맛집",
            ),
        ]


class FakeNaverBlogProvider(NaverBlogEvidenceProvider):
    def __init__(self, settings: Settings) -> None:
        super().__init__(settings)
        self.endpoints: list[str] = []

    def search(self, endpoint: str, query: str, display: int = 10, sort: str = "sim") -> dict[str, Any]:
        self.endpoints.append(endpoint)
        if endpoint != "blog":
            raise AssertionError(f"unexpected endpoint: {endpoint}")
        return {
            "items": [
                {
                    "title": "목동한식당 방문 후기",
                    "description": "목동한식당에서 직접 먹고 온 후기입니다.",
                    "postdate": "20260420",
                    "bloggername": "food",
                    "link": "https://blog.naver.com/food/korean",
                },
            ]
        }


class NoMatchNaverBlogProvider(FakeNaverBlogProvider):
    def search(self, endpoint: str, query: str, display: int = 10, sort: str = "sim") -> dict[str, Any]:
        self.endpoints.append(endpoint)
        return {
            "items": [
                {
                    "title": "다른가게 방문 후기",
                    "description": "목동 근처 다른가게 후기입니다.",
                    "postdate": "20260420",
                    "bloggername": "food",
                    "link": "https://blog.naver.com/food/other",
                },
            ]
        }
