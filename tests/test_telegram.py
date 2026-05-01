from momukbot.chat.telegram import chunk_text


def test_chunk_text_splits_long_message() -> None:
    chunks = chunk_text("a" * 5000, 1000)

    assert len(chunks) == 5
    assert all(len(chunk) <= 1000 for chunk in chunks)
