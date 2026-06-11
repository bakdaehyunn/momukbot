from momukbot.core.matching import blog_text_matches_name


def test_blog_match_requires_actual_brand_for_branch_place_names() -> None:
    assert not blog_text_matches_name(
        "수변최고돼지국밥 서면롯데점",
        "부산 서면 해장하기 좋은 얼큰 국밥 맛집 수백당 소주 2500원 "
        "서면수백당 수백당서면롯데점 얼큰국밥 후기",
    )


def test_blog_match_accepts_brand_token_for_branch_place_names() -> None:
    assert blog_text_matches_name(
        "조선돼지국밥 서면점",
        "서면 돼지국밥 맛집 24시간 해장까지 가능한 조선돼지국밥 후기",
    )
