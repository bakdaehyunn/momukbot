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


@dataclass(frozen=True)
class RequestLocation:
    latitude: float
    longitude: float
    label: str = ""
    radius_m: int = 1500


@dataclass
class RecommendationItem:
    name: str
    category: str = ""
    status_marker: str = "영업시간 미확인"
    reason: str = ""
    links: list[dict[str, str]] = field(default_factory=list)
    fit_tags: list[str] = field(default_factory=list)
    tradeoff: str = ""
    intent_fit: int = 0
    meal_fit: int = 0
    occasion_fit: int = 0
    evidence_quality: int = 0
    risk_flags: list[str] = field(default_factory=list)
    menu_family: str = ""
    best_for: str = ""
    diversity_group: str = ""
    confidence: int | None = None
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
    place_id: str = ""
    phone: str = ""
    category_group_code: str = ""
    category_group_name: str = ""
    x: str = ""
    y: str = ""
    distance: str = ""
    selected_region: str = ""
    region_candidates: tuple[str, ...] = ()


@dataclass(frozen=True)
class EvidenceBundle:
    source: str
    title: str
    summary: str
    url: str
    postdate: str = ""
    author: str = ""
    score: int = 0
    signals: tuple[str, ...] = ()
    penalties: tuple[str, ...] = ()
    original_index: int = 0

    @property
    def text(self) -> str:
        return " ".join(part for part in (self.title, self.summary) if part).strip()


@dataclass(frozen=True)
class EvidenceMetrics:
    matched_post_count: int = 0
    recent_post_count: int = 0
    unique_author_count: int = 0
    title_match_count: int = 0
    summary_match_count: int = 0
    snippet_signal_count: int = 0
    ad_like_count: int = 0
    stale_post_count: int = 0
    aggregate_score: int = 0
    best_score: int = 0


@dataclass(frozen=True)
class VerifiedCandidate:
    candidate: SearchCandidate
    evidence: tuple[EvidenceBundle, ...] = ()
    metrics: EvidenceMetrics = field(default_factory=EvidenceMetrics)
    source: str = "kakao_local+naver_blog"


@dataclass(frozen=True)
class SearchPlan:
    area: str = ""
    topic: str = ""
    count: int = 30
    context_hint: str = ""
    location_mode: bool = False
    area_label: str = ""
    kakao_source: str = "kakao_local"
    evidence_source: str = "naver_blog"


class DropReason:
    NO_KAKAO_URL = "no_kakao_url"
    MISSING_CANDIDATE = "missing_candidate"
    DUPLICATE = "duplicate"
    MISSING_EVIDENCE = "missing_evidence"
    CAFE_FAST_FOOD_EXCLUDED = "cafe_fast_food_excluded"
    EXACT_FOOD_MISMATCH = "exact_food_mismatch"
    WEAK_FIT = "weak_fit"


@dataclass(frozen=True)
class SearchContext:
    text: str = ""
    used_provider: str = ""
    quota_blocked: bool = False
    configured: bool = False
    evidence_available: bool = True
    candidates: list[SearchCandidate] = field(default_factory=list)
    verified_candidates: list[VerifiedCandidate] = field(default_factory=list)
    search_plan: SearchPlan | None = None
    stats: dict[str, int] = field(default_factory=dict)
