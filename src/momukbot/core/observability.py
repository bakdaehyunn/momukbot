from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


class RecommendationOutcome:
    OK = "ok"
    DRY_RUN = "dry_run"
    IGNORED = "ignored"
    LOCATION_REQUIRED = "location_required"
    MISSING_AREA = "missing_area"
    SEARCH_CONTEXT_FAILED = "search_context_failed"
    KAKAO_NO_CANDIDATES = "kakao_no_candidates"
    BLOG_NO_MATCH = "blog_no_match"
    BLOG_API_FAILED = "blog_api_failed"
    EVIDENCE_UNAVAILABLE = "evidence_unavailable"
    AGENT_GENERATE_FAILED = "agent_generate_failed"
    INVALID_AGENT_RESPONSE = "invalid_agent_response"
    NO_CONFIRMED_BLOG_EVIDENCE = "no_confirmed_blog_evidence"
    EMPTY_RESULT = "empty_result"


@dataclass
class RecommendationEvent:
    request_id: str
    outcome: str
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat(timespec="seconds"))
    chat_id: str = ""
    parse_source: str = ""
    parse_reason: str = ""
    parsed_intent: str = ""
    parsed_area: str = ""
    parsed_topic: str = ""
    parsed_count: int = 0
    kakao_candidate_count: int = 0
    naver_blog_evidence_count: int = 0
    matched_candidate_count: int = 0
    final_item_count: int = 0
    target_count: int = 0
    total_ms: int = 0
    stage_ms: dict[str, int] = field(default_factory=dict)
    failure_reason: str = ""
    partial: bool = False
    location_mode: bool = False
    dry_run: bool = False

    def to_record(self) -> dict[str, Any]:
        return {
            "request_id": self.request_id,
            "created_at": self.created_at,
            "chat_id": self.chat_id,
            "outcome": self.outcome,
            "failure_reason": self.failure_reason,
            "parse_source": self.parse_source,
            "parse_reason": self.parse_reason,
            "parsed_intent": self.parsed_intent,
            "parsed_area": self.parsed_area,
            "parsed_topic": self.parsed_topic,
            "parsed_count": self.parsed_count,
            "target_count": self.target_count,
            "kakao_candidate_count": self.kakao_candidate_count,
            "naver_blog_evidence_count": self.naver_blog_evidence_count,
            "matched_candidate_count": self.matched_candidate_count,
            "final_item_count": self.final_item_count,
            "partial": self.partial,
            "location_mode": self.location_mode,
            "dry_run": self.dry_run,
            "total_ms": self.total_ms,
            "stage_ms": dict(self.stage_ms),
        }


def classify_evidence_unavailable(text: str) -> str:
    if "No Kakao Local candidates had matching Naver Blog evidence" in text:
        return RecommendationOutcome.BLOG_NO_MATCH
    if "No Kakao Local candidates" in text:
        return RecommendationOutcome.KAKAO_NO_CANDIDATES
    if "Naver Blog search failed" in text:
        return RecommendationOutcome.BLOG_API_FAILED
    return RecommendationOutcome.EVIDENCE_UNAVAILABLE
