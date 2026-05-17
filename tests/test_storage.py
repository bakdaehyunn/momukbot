from momukbot.core.models import RecommendationItem
from momukbot.storage.sqlite import RecommendationStore


def test_store_clear_removes_recommendation_history(tmp_path) -> None:
    store = RecommendationStore(tmp_path)
    store.add_result(
        chat_id="123",
        request_text="서면 국밥 추천",
        area="서면",
        topic="국밥",
        search_keyword="서면 국밥",
        raw_response="raw",
        items=[RecommendationItem(name="송정3대국밥")],
    )

    assert store.clear() == 1

    with store.connect() as conn:
        count = conn.execute("SELECT COUNT(*) FROM recommendations").fetchone()[0]
    assert count == 0
