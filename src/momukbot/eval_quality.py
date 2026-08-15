from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from momukbot.config import ROOT, Settings
from momukbot.core.models import (
    EvidenceBundle,
    ParsedRequest,
    RecommendationItem,
    SearchCandidate,
    SearchContext,
    VerifiedCandidate,
)
from momukbot.core.service import RecommendationService


DEFAULT_QUALITY_FIXTURE = ROOT / "quality" / "recommendation_quality.json"


@dataclass(frozen=True)
class QualityCheck:
    name: str
    passed: bool
    detail: str = ""


@dataclass(frozen=True)
class QualityCaseResult:
    name: str
    request_text: str
    final_names: tuple[str, ...]
    checks: tuple[QualityCheck, ...]
    response: str = ""

    @property
    def passed(self) -> bool:
        return all(check.passed for check in self.checks)


@dataclass(frozen=True)
class QualityReport:
    fixture_path: Path
    cases: tuple[QualityCaseResult, ...]

    @property
    def passed_count(self) -> int:
        return sum(1 for case in self.cases if case.passed)

    @property
    def failed_count(self) -> int:
        return len(self.cases) - self.passed_count

    @property
    def passed(self) -> bool:
        return self.failed_count == 0


class FixtureAgent:
    def __init__(self, response: object) -> None:
        self.response = response
        self.prompts: list[str] = []

    def generate(self, prompt: str) -> str:
        self.prompts.append(prompt)
        if isinstance(self.response, str):
            return self.response
        return json.dumps(self.response, ensure_ascii=False)


class FixtureSearchProvider:
    def __init__(self, candidates: list[SearchCandidate], verified_candidates: list[VerifiedCandidate]) -> None:
        self.candidates = candidates
        self.verified_candidates = verified_candidates

    def build_context(
        self,
        area: str,
        topic: str,
        count: int = 30,
        context_hint: str = "",
        location: object | None = None,
    ) -> SearchContext:
        evidence_count = sum(len(candidate.evidence) for candidate in self.verified_candidates)
        return SearchContext(
            text=_format_fixture_context(self.verified_candidates),
            used_provider="fixture",
            configured=True,
            evidence_available=bool(self.verified_candidates),
            candidates=self.candidates,
            verified_candidates=self.verified_candidates,
            stats={
                "kakao_candidate_count": len(self.candidates),
                "naver_blog_evidence_count": evidence_count,
                "matched_candidate_count": len(self.verified_candidates),
            },
        )


class QualityRecordingStore:
    def __init__(self) -> None:
        self.items: list[RecommendationItem] = []

    def add_result(
        self,
        chat_id: str,
        request_text: str,
        area: str,
        topic: str,
        search_keyword: str,
        raw_response: str,
        items: list[RecommendationItem],
    ) -> None:
        self.items = list(items)


def run_quality_fixture(path: Path, settings: Settings) -> QualityReport:
    payload = json.loads(path.read_text(encoding="utf-8"))
    raw_cases = payload.get("cases") if isinstance(payload, dict) else None
    if not isinstance(raw_cases, list):
        raise ValueError("quality fixture must contain a cases array")
    cases = tuple(_run_quality_case(raw_case, settings) for raw_case in raw_cases)
    return QualityReport(fixture_path=path, cases=cases)


def format_quality_report(report: QualityReport) -> str:
    lines = [
        (
            f"quality_cases={len(report.cases)} passed={report.passed_count} "
            f"failed={report.failed_count} fixture={report.fixture_path}"
        )
    ]
    for case in report.cases:
        status = "PASS" if case.passed else "FAIL"
        lines.append(f"[{status}] {case.name} final={', '.join(case.final_names) or '(none)'}")
        for check in case.checks:
            check_status = "ok" if check.passed else "fail"
            detail = f" {check.detail}" if check.detail else ""
            lines.append(f"  - {check.name}: {check_status}{detail}")
    return "\n".join(lines)


def _run_quality_case(raw_case: object, settings: Settings) -> QualityCaseResult:
    if not isinstance(raw_case, dict):
        raise ValueError("quality fixture cases must be objects")
    name = _required_text(raw_case, "name")
    parsed = _parsed_request(raw_case.get("parsed"))
    request_text = str(raw_case.get("request_text") or f"{parsed.area} {parsed.topic} 추천").strip()
    candidates, verified_candidates = _fixture_candidates(raw_case.get("candidates"))
    store = QualityRecordingStore()
    service = RecommendationService(
        settings,
        FixtureAgent(raw_case.get("agent_response") or {}),
        FixtureSearchProvider(candidates, verified_candidates),
        store,  # type: ignore[arg-type]
    )
    response = service.recommend(
        chat_id="quality-eval",
        request_text=request_text,
        parsed=parsed,
        dry_run=False,
    )
    final_names = tuple(item.name for item in store.items)
    checks = tuple(_quality_checks(final_names, raw_case.get("expect", {})))
    return QualityCaseResult(
        name=name,
        request_text=request_text,
        final_names=final_names,
        checks=checks,
        response=response,
    )


def _quality_checks(final_names: tuple[str, ...], raw_expect: object) -> list[QualityCheck]:
    expect = raw_expect if isinstance(raw_expect, dict) else {}
    checks: list[QualityCheck] = []
    if "count" in expect:
        count = int(expect["count"])
        checks.append(
            QualityCheck(
                name="count",
                passed=len(final_names) == count,
                detail=f"expected={count} actual={len(final_names)}",
            )
        )
    if "min_count" in expect:
        count = int(expect["min_count"])
        checks.append(
            QualityCheck(
                name="min_count",
                passed=len(final_names) >= count,
                detail=f"expected>={count} actual={len(final_names)}",
            )
        )
    expected_top = _text_list(expect.get("top"))
    if expected_top:
        actual_top = list(final_names[: len(expected_top)])
        checks.append(
            QualityCheck(
                name="top",
                passed=actual_top == expected_top,
                detail=f"expected={expected_top} actual={actual_top}",
            )
        )
    for name in _text_list(expect.get("include")):
        checks.append(
            QualityCheck(
                name=f"include:{name}",
                passed=name in final_names,
                detail="" if name in final_names else "missing",
            )
        )
    for name in _text_list(expect.get("exclude")):
        checks.append(
            QualityCheck(
                name=f"exclude:{name}",
                passed=name not in final_names,
                detail="" if name not in final_names else "present",
            )
        )
    max_positions = expect.get("max_position")
    if isinstance(max_positions, dict):
        for name, raw_position in max_positions.items():
            position = int(raw_position)
            actual = final_names.index(name) + 1 if name in final_names else 0
            checks.append(
                QualityCheck(
                    name=f"max_position:{name}",
                    passed=0 < actual <= position,
                    detail=f"expected<={position} actual={actual or 'missing'}",
                )
            )
    order_pairs = expect.get("order")
    if isinstance(order_pairs, list):
        for pair in order_pairs:
            if not isinstance(pair, (list, tuple)) or len(pair) != 2:
                continue
            before, after = str(pair[0]), str(pair[1])
            passed = before in final_names and after in final_names and final_names.index(before) < final_names.index(after)
            checks.append(
                QualityCheck(
                    name=f"order:{before}<:{after}",
                    passed=passed,
                    detail="" if passed else "order_missing_or_reversed",
                )
            )
    if not checks:
        checks.append(QualityCheck(name="has_results", passed=bool(final_names)))
    return checks


def _fixture_candidates(raw_candidates: object) -> tuple[list[SearchCandidate], list[VerifiedCandidate]]:
    if not isinstance(raw_candidates, list):
        raise ValueError("quality fixture case must contain a candidates array")
    candidates: list[SearchCandidate] = []
    verified_candidates: list[VerifiedCandidate] = []
    for index, raw_candidate in enumerate(raw_candidates, start=1):
        if not isinstance(raw_candidate, dict):
            raise ValueError("quality fixture candidates must be objects")
        candidate = SearchCandidate(
            name=_required_text(raw_candidate, "name"),
            category=str(raw_candidate.get("category") or "기타"),
            raw_category=str(raw_candidate.get("raw_category") or raw_candidate.get("category") or ""),
            address=str(raw_candidate.get("address") or f"테스트 주소 {index}"),
            url=str(raw_candidate.get("url") or f"https://place.map.kakao.com/{1000 + index}"),
            source="kakao_local",
            query=str(raw_candidate.get("query") or ""),
        )
        candidates.append(candidate)
        evidence = tuple(
            _evidence_bundle(raw, index, evidence_index)
            for evidence_index, raw in enumerate(raw_candidate.get("evidence") or [], start=1)
        )
        if evidence:
            verified_candidates.append(VerifiedCandidate(candidate=candidate, evidence=evidence))
    return candidates, verified_candidates


def _evidence_bundle(raw: object, candidate_index: int, evidence_index: int) -> EvidenceBundle:
    data = raw if isinstance(raw, dict) else {}
    return EvidenceBundle(
        source="naver_blog",
        title=str(data.get("title") or f"후기 {candidate_index}-{evidence_index}"),
        summary=str(data.get("summary") or "방문 후기"),
        url=str(data.get("url") or f"https://blog.naver.com/quality/{candidate_index}-{evidence_index}"),
        postdate=str(data.get("postdate") or "20260801"),
        author=str(data.get("author") or f"tester{candidate_index}{evidence_index}"),
        score=int(data.get("score") or 0),
        signals=tuple(str(item) for item in data.get("signals") or ()),
        penalties=tuple(str(item) for item in data.get("penalties") or ()),
        original_index=evidence_index,
    )


def _parsed_request(raw: object) -> ParsedRequest:
    data = raw if isinstance(raw, dict) else {}
    return ParsedRequest(
        intent=str(data.get("intent") or "start"),
        area=str(data.get("area") or ""),
        topic=str(data.get("topic") or "맛집"),
        meal_type=str(data.get("meal_type") or ""),
        budget=str(data.get("budget") or ""),
        occasion=str(data.get("occasion") or ""),
        count=max(1, min(30, int(data.get("count") or 30))),
    )


def _format_fixture_context(verified_candidates: list[VerifiedCandidate]) -> str:
    lines = ["Verified Kakao Local + Naver Blog evidence matches. Use only these candidates:"]
    for index, verified in enumerate(verified_candidates, start=1):
        candidate = verified.candidate
        lines.append(f"{index}. place={candidate.name} category={candidate.category} address={candidate.address}")
        for evidence_index, evidence in enumerate(verified.evidence, start=1):
            lines.append(
                f"{index}.{evidence_index} place={candidate.name} blog_score={evidence.score} "
                f"postdate={evidence.postdate} blog_url={evidence.url} "
                f"blog_title={evidence.title} blog_summary={evidence.summary}"
            )
    return "\n".join(lines)


def _required_text(data: dict[str, object], key: str) -> str:
    value = str(data.get(key) or "").strip()
    if not value:
        raise ValueError(f"quality fixture missing required {key}")
    return value


def _text_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]
