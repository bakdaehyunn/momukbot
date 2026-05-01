import pytest

from momukbot.storage.quota import JsonQuotaGuard, QuotaExceeded


def test_quota_blocks_at_soft_limit(tmp_path) -> None:
    guard = JsonQuotaGuard(tmp_path, soft_limit=1, configured=True)

    guard.reserve("blog", "서면")

    with pytest.raises(QuotaExceeded):
        guard.reserve("local", "서면")

    status = guard.status()
    assert status.count == 1
    assert status.remaining == 0
