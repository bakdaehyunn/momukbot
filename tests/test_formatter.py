from momukbot.core.formatter import filter_preferred_links, format_recommendation_message, naver_map_search_url
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
    assert "   참고 블로그: https://blog.naver.com/a/b" in text
    assert "   네이버 지도: https://map.naver.com/p/search/%EC%84%9C%EB%A9%B4%20%EC%86%A1%EC%A0%953%EB%8C%80%EA%B5%AD%EB%B0%A5" in text


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
    assert "참고 블로그:" not in text
    assert "네이버 지도:" in text


def test_naver_map_url_encodes_name() -> None:
    assert naver_map_search_url("송정3대국밥").startswith("https://map.naver.com/p/search/")


def test_naver_map_url_includes_area_without_duplicate() -> None:
    assert naver_map_search_url("송정3대국밥", area="서면").endswith(
        "/%EC%84%9C%EB%A9%B4%20%EC%86%A1%EC%A0%953%EB%8C%80%EA%B5%AD%EB%B0%A5"
    )
    assert naver_map_search_url("서면 송정3대국밥", area="서면").endswith(
        "/%EC%84%9C%EB%A9%B4%20%EC%86%A1%EC%A0%953%EB%8C%80%EA%B5%AD%EB%B0%A5"
    )
