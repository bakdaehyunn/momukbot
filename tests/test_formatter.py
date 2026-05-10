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
    assert links[0]["label"] == "블로그"
    assert links[1]["url"] == "https://example.com/place"


def test_filter_preferred_links_rejects_tistory() -> None:
    links = filter_preferred_links(
        [{"label": "글", "url": "https://abc.tistory.com/1"}],
    )

    assert links == []


def test_format_groups_categories_and_adds_map_link() -> None:
    text = format_recommendation_message(
        "서면 해장",
        [
            RecommendationItem(name="송정3대국밥", category="국밥", reason="후기 신호가 좋습니다."),
            RecommendationItem(name="청진동감자탕", category="감자탕", reason="뼈해장국 후보입니다."),
        ],
    )

    assert "[국밥]" in text
    assert "[감자탕/뼈해장국]" in text
    assert "네이버지도:" in text


def test_naver_map_url_encodes_name() -> None:
    assert naver_map_search_url("송정3대국밥").startswith("https://map.naver.com/p/search/")
