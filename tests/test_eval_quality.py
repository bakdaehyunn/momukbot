import json
from pathlib import Path

from momukbot.config import Settings
from momukbot.eval_quality import DEFAULT_QUALITY_FIXTURE, format_quality_report, run_quality_fixture


def test_default_quality_fixture_passes(tmp_path: Path) -> None:
    report = run_quality_fixture(DEFAULT_QUALITY_FIXTURE, settings(tmp_path))

    assert report.passed is True
    assert report.passed_count == 2
    assert report.failed_count == 0
    assert report.cases[0].final_names[:2] == ("조용한밥집", "든든국밥")


def test_quality_fixture_reports_failed_expectations(tmp_path: Path) -> None:
    fixture = tmp_path / "quality.json"
    fixture.write_text(
        json.dumps(
            {
                "cases": [
                    {
                        "name": "wrong top candidate",
                        "parsed": {"area": "서면", "topic": "맛집", "count": 1},
                        "candidates": [
                            {
                                "name": "조용한밥집",
                                "category": "한식",
                                "evidence": [{"url": "https://blog.naver.com/q/1"}],
                            }
                        ],
                        "agent_response": {
                            "search_keyword": "서면 맛집",
                            "evaluations": [
                                {
                                    "name": "조용한밥집",
                                    "category": "한식",
                                    "status_marker": "영업시간 미확인",
                                    "intent_fit": 5,
                                    "meal_fit": 5,
                                    "occasion_fit": 5,
                                    "evidence_quality": 5,
                                    "confidence": 5,
                                    "risk_flags": [],
                                    "menu_family": "백반",
                                    "best_for": "혼밥",
                                    "diversity_group": "백반",
                                    "fit_tags": ["혼밥"],
                                    "tradeoff": "",
                                    "reason": "혼밥에 맞습니다.",
                                }
                            ],
                        },
                        "expect": {"top": ["다른가게"]},
                    }
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    report = run_quality_fixture(fixture, settings(tmp_path))
    text = format_quality_report(report)

    assert report.passed is False
    assert report.failed_count == 1
    assert "[FAIL] wrong top candidate" in text
    assert "expected=['다른가게'] actual=['조용한밥집']" in text


def settings(tmp_path: Path) -> Settings:
    return Settings(
        telegram_bot_token="",
        telegram_allowed_chat_ids=(),
        telegram_admin_user_ids=(),
        naver_client_id="",
        naver_client_secret="",
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
