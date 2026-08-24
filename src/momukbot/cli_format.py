from __future__ import annotations

import json

from momukbot.core.llm_parser import RequestParseResult


def print_commands(commands: list[dict[str, str]]) -> None:
    if not commands:
        print("(empty)")
        return
    for item in commands:
        print(f"/{item.get('command', '')} - {item.get('description', '')}")


def format_parse_debug(result: RequestParseResult) -> str:
    parsed = result.parsed
    lines = [
        f"parse_source={result.source}",
        f"parse_reason={result.reason}",
        f"llm_used={str(result.llm_used).lower()}",
        f"llm_raw_chars={result.llm_raw_chars}",
        f"intent={parsed.intent}",
        f"area={parsed.area}",
        f"topic={parsed.topic}",
        f"meal_type={parsed.meal_type}",
        f"budget={parsed.budget}",
        f"occasion={parsed.occasion}",
        f"count={parsed.count}",
    ]
    return "\n".join(lines)


def parse_debug_json(result: RequestParseResult) -> str:
    parsed = result.parsed
    return json.dumps(
        {
            "parse_source": result.source,
            "parse_reason": result.reason,
            "llm_used": result.llm_used,
            "llm_raw_chars": result.llm_raw_chars,
            "parsed": {
                "intent": parsed.intent,
                "area": parsed.area,
                "topic": parsed.topic,
                "meal_type": parsed.meal_type,
                "budget": parsed.budget,
                "occasion": parsed.occasion,
                "count": parsed.count,
            },
        },
        ensure_ascii=False,
        sort_keys=True,
    )


def quality_report_json(report) -> str:
    return json.dumps(
        {
            "fixture": str(report.fixture_path),
            "passed": report.passed,
            "passed_count": report.passed_count,
            "failed_count": report.failed_count,
            "cases": [
                {
                    "name": case.name,
                    "request_text": case.request_text,
                    "passed": case.passed,
                    "final_names": list(case.final_names),
                    "checks": [
                        {
                            "name": check.name,
                            "passed": check.passed,
                            "detail": check.detail,
                        }
                        for check in case.checks
                    ],
                }
                for case in report.cases
            ],
        },
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    )


def format_recent_events(records: list[dict[str, object]]) -> str:
    if not records:
        return "(no recommendation events)"
    lines: list[str] = []
    for record in records:
        lines.append(
            " ".join(
                [
                    f"created_at={record.get('created_at', '')}",
                    f"outcome={record.get('outcome', '')}",
                    f"failure_reason={record.get('failure_reason', '')}",
                    f"parse_source={record.get('parse_source', '')}",
                    f"parse_reason={record.get('parse_reason', '')}",
                    f"area={record.get('parsed_area', '')}",
                    f"topic={record.get('parsed_topic', '')}",
                    f"matched_candidate_count={record.get('matched_candidate_count', 0)}",
                    f"final_item_count={record.get('final_item_count', 0)}",
                    f"missing_item_count={record.get('missing_item_count', 0)}",
                    f"agent_generate_ms={record.get('agent_generate_ms', 0)}",
                    f"total_ms={record.get('total_ms', 0)}",
                ]
            )
        )
    return "\n".join(lines)
