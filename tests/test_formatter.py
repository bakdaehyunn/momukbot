from momukbot.core.formatter import (
    filter_preferred_links,
    format_recommendation_message,
    naver_map_search_url,
    preferred_naver_map_url,
)
from momukbot.core.models import RecommendationItem


def test_filter_preferred_links_prioritizes_naver_blog_only() -> None:
    links = filter_preferred_links(
        [
            {"label": "지도", "url": "https://example.com/place"},
            {"label": "글", "url": "https://blog.naver.com/some/post"},
            {"label": "글", "url": "https://abc.tistory.com/1"},
        ]
    )

    assert links[0]["url"].startswith("https://blog.naver.com")
    assert links[0]["label"] == "네이버 블로그"
    assert len(links) == 1


def test_filter_preferred_links_rejects_non_naver_blog_fallback() -> None:
    links = filter_preferred_links(
        [
            {"label": "지도", "url": "https://example.com/place"},
            {"label": "공식", "url": "https://restaurant.example.com"},
        ]
    )

    assert links == []


def test_filter_preferred_links_rejects_tistory() -> None:
    links = filter_preferred_links(
        [{"label": "글", "url": "https://abc.tistory.com/1"}],
    )

    assert links == []


def test_format_groups_categories_and_adds_map_link() -> None:
    text = format_recommendation_message(
        "서면 해장",
        [
            RecommendationItem(
                name="송정3대국밥",
                category="국밥",
                status_marker="영업 가능성 높음",
                reason="후기 신호가 좋습니다.",
                links=[{"label": "블로그", "url": "https://blog.naver.com/a/b"}],
            ),
            RecommendationItem(
                name="청진동감자탕",
                category="감자탕",
                status_marker="영업시간 미확인",
                reason="뼈해장국 후보입니다.",
            ),
        ],
        area="서면",
    )

    assert text.startswith("서면 해장 추천 2곳")
    assert "[국밥]" in text
    assert "[감자탕/뼈해장국]" in text
    assert "1. 송정3대국밥" in text
    assert "   상태: 영업 가능성 높음" in text
    assert "   이유: 후기 신호가 좋습니다." in text
    assert "   블로그: https://blog.naver.com/a/b" in text
    assert (
        "   지도: https://map.naver.com/p/search/%EC%84%9C%EB%A9%B4%20%EC%86%A1%EC%A0%953%EB%8C%80%EA%B5%AD%EB%B0%A5"
        in text
    )
    assert "app.map.naver.com/launchApp" not in text


def test_format_uses_naver_local_map_details_when_available() -> None:
    text = format_recommendation_message(
        "목동역 맛집",
        [
            RecommendationItem(
                name="맥도날드 목동점",
                category="기타",
                reason="주소와 지도 링크 확인용입니다.",
                map_name="맥도날드 목동점",
                map_address="서울 양천구 목동로 221",
                map_url="https://naver.me/FAjSYD1g",
            ),
        ],
        area="목동역",
    )

    assert (
        "   주소: 서울 양천구 목동로 221\n"
        "   지도: https://naver.me/FAjSYD1g"
        in text
    )
    assert "map.naver.com/p/search" not in text


def test_format_adds_llm_reasoning_summary_and_item_tradeoffs() -> None:
    text = format_recommendation_message(
        "서면 혼밥",
        [
            RecommendationItem(
                name="조용한밥집",
                category="한식",
                status_marker="영업시간 미확인",
                reason="혼밥 후기가 있어 요청에 잘 맞습니다.",
                fit_tags=["혼밥", "조용함"],
                tradeoff="영업시간은 확인되지 않았습니다.",
                links=[{"label": "네이버 블로그", "url": "https://blog.naver.com/a/b"}],
            ),
            RecommendationItem(name="든든국밥", category="국밥"),
            RecommendationItem(name="고기집", category="한식"),
        ],
        area="서면",
        decision_criteria=["혼밥", "조용함"],
        top_summary="혼밥 가능성과 조용한 분위기를 우선했습니다.",
    )

    assert "이번 요청 기준: 혼밥, 조용함" in text
    assert "혼밥 가능성과 조용한 분위기를 우선했습니다." in text
    assert "먼저 볼 3곳: 조용한밥집, 든든국밥, 고기집" in text
    assert "[추천 순서]" in text
    assert text.find("1. 조용한밥집") < text.find("2. 든든국밥") < text.find("3. 고기집")
    assert "   포인트: 혼밥 · 조용함" in text
    assert "   참고: 영업시간은 확인되지 않았습니다." in text
    assert "   상태: 영업시간 미확인" not in text


def test_format_ignores_non_naver_blog_item_links() -> None:
    text = format_recommendation_message(
        "서면 해장",
        [
            RecommendationItem(
                name="송정3대국밥",
                category="국밥",
                links=[{"label": "공식", "url": "https://example.com/place"}],
            ),
        ],
        area="서면",
    )

    assert "example.com" not in text
    assert "블로그:" not in text
    assert "지도:" in text


def test_naver_map_url_encodes_name() -> None:
    assert naver_map_search_url("송정3대국밥") == (
        "https://map.naver.com/p/search/%EC%86%A1%EC%A0%953%EB%8C%80%EA%B5%AD%EB%B0%A5"
    )


def test_naver_map_url_includes_area_without_duplicate() -> None:
    assert naver_map_search_url("송정3대국밥", area="서면") == (
        "https://map.naver.com/p/search/%EC%84%9C%EB%A9%B4%20%EC%86%A1%EC%A0%953%EB%8C%80%EA%B5%AD%EB%B0%A5"
    )
    assert naver_map_search_url("서면 송정3대국밥", area="서면") == (
        "https://map.naver.com/p/search/%EC%84%9C%EB%A9%B4%20%EC%86%A1%EC%A0%953%EB%8C%80%EA%B5%AD%EB%B0%A5"
    )


def test_preferred_naver_map_url_rejects_non_naver_local_link() -> None:
    url = preferred_naver_map_url(
        "https://instagram.com/place",
        "맥도날드 목동점",
        area="목동역",
    )

    assert url == (
        "https://map.naver.com/p/search/%EB%AA%A9%EB%8F%99%EC%97%AD%20%EB%A7%A5%EB%8F%84%EB%82%A0%EB%93%9C%20%EB%AA%A9%EB%8F%99%EC%A0%90"
    )
    assert "instagram.com" not in url


def test_preferred_naver_map_url_rejects_unreliable_app_wrapper() -> None:
    url = preferred_naver_map_url(
        "https://app.map.naver.com/launchApp/?version=11&menu=search&query=foo",
        "맥도날드 목동점",
        area="목동역",
    )

    assert url.startswith("https://map.naver.com/p/search/")
    assert "app.map.naver.com" not in url


def test_preferred_naver_map_url_rejects_non_clickable_nmap_scheme() -> None:
    url = preferred_naver_map_url(
        "nmap://search?query=%EB%AA%A9%EB%8F%99%EC%97%AD&appname=momukbot",
        "맥도날드 목동점",
        area="목동역",
    )

    assert url.startswith("https://map.naver.com/p/search/")
    assert "nmap://" not in url
