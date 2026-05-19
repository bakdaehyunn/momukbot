from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class ParsedRequest:
    intent: str
    area: str = ""
    topic: str = ""
    meal_type: str = ""
    budget: str = ""
    occasion: str = ""
    count: int = 30


@dataclass
class RecommendationItem:
    name: str
    category: str = ""
    status_marker: str = "영업시간 미확인"
    reason: str = ""
    links: list[dict[str, str]] = field(default_factory=list)
    fit_tags: list[str] = field(default_factory=list)
    tradeoff: str = ""
    map_name: str = ""
    map_address: str = ""
    map_url: str = ""


@dataclass
class RecommendationResult:
    search_keyword: str = ""
    items: list[RecommendationItem] = field(default_factory=list)
    decision_criteria: list[str] = field(default_factory=list)
    top_summary: str = ""
    raw_text: str = ""
    raw_json: dict[str, Any] | None = None


@dataclass(frozen=True)
class SearchCandidate:
    name: str
    category: str = ""
    raw_category: str = ""
    address: str = ""
    url: str = ""
    source: str = ""
    query: str = ""


@dataclass(frozen=True)
class SearchContext:
    text: str = ""
    used_provider: str = ""
    quota_blocked: bool = False
    configured: bool = False
    evidence_available: bool = True
    candidates: list[SearchCandidate] = field(default_factory=list)
