from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any
from urllib.parse import urlencode, urlparse
from urllib.request import Request, urlopen

from momukbot.config import Settings
from momukbot.core.matching import blog_text_matches_name, normalize_match_text
from momukbot.core.models import SearchCandidate
from momukbot.search.candidates import _context_terms, _dedupe, clean_html
from momukbot.storage.quota import JsonQuotaGuard


class NaverNotConfigured(RuntimeError):
    pass


VISIT_REVIEW_WORDS = ("방문", "다녀왔", "먹고", "주문", "웨이팅", "내돈내산")
OPEN_STATUS_WORDS = ("24시", "새벽", "늦게", "영업시간", "라스트오더", "심야", "야간")
UNLIMITED_REVIEW_WORDS = (
    "무한리필",
    "무제한",
    "뷔페",
    "부페",
    "샐러드바",
    "리필",
    "월남쌈",
    "샤브샤브",
    "편백찜",
    "시간제한",
    "1인 가격",
)
AD_WORDS = ("협찬", "제공받아", "체험단", "원고료", "광고")
ROUNDUP_WORDS = ("best", "BEST", "총정리", "모음", "리스트")
TARGETED_BLOG_SEARCH_LIMIT = 30
TARGETED_BLOG_DISPLAY = 10
SECONDARY_BLOG_DISPLAY = 50
SECOND_WAVE_MATCH_NUMERATOR = 4
SECOND_WAVE_MATCH_DENOMINATOR = 5
BLOG_EVIDENCE_PER_CANDIDATE = 2
MIN_SUPPORTING_BLOG_SCORE = 0
CONTEXT_TEXT_LIMIT = 120
AREA_VARIANT_SUFFIXES = (
    "센트럴파크",
    "해수욕장",
    "한옥마을",
    "터미널",
    "대학가",
    "공항",
    "시장",
    "입구",
    "거리",
    "대로",
    "번가",
    "역",
    "동",
    "로",
    "길",
    "구",
    "시",
    "군",
    "읍",
    "면",
    "리",
    "도",
)
AREA_LANDMARK_SUFFIXES = ("해수욕장", "한옥마을", "센트럴파크")

@dataclass(frozen=True)
class BlogEvidence:
    title: str
    summary: str
    postdate: str
    blogger: str
    url: str
    score: int
    signals: tuple[str, ...] = field(default_factory=tuple)
    penalties: tuple[str, ...] = field(default_factory=tuple)
    original_index: int = 0


@dataclass(frozen=True)
class LocalBlogMatch:
    candidate: SearchCandidate
    evidence: tuple[BlogEvidence, ...]
    candidate_index: int
    matched_post_count: int = 0
    recent_post_count: int = 0
    unique_blogger_count: int = 0
    title_match_count: int = 0
    summary_match_count: int = 0
    snippet_signal_count: int = 0
    ad_like_count: int = 0
    stale_post_count: int = 0
    aggregate_score: int = 0

    @property
    def best_score(self) -> int:
        return self.evidence[0].score if self.evidence else 0


def build_blog_evidence(
    item: dict[str, Any],
    area: str,
    topic: str,
    original_index: int = 0,
    today: date | None = None,
) -> BlogEvidence:
    title = clean_html(str(item.get("title") or ""))
    summary = clean_html(str(item.get("description") or ""))
    postdate = str(item.get("postdate") or "").strip()
    blogger = clean_html(str(item.get("bloggername") or ""))
    url = str(item.get("link") or "").strip()
    score, signals, penalties = score_blog_evidence(
        title=title,
        summary=summary,
        postdate=postdate,
        area=area,
        topic=topic,
        today=today,
    )
    return BlogEvidence(
        title=title,
        summary=summary,
        postdate=postdate,
        blogger=blogger,
        url=url,
        score=score,
        signals=tuple(signals),
        penalties=tuple(penalties),
        original_index=original_index,
    )


def score_blog_evidence(
    title: str,
    summary: str,
    postdate: str,
    area: str,
    topic: str,
    today: date | None = None,
) -> tuple[int, list[str], list[str]]:
    today = today or date.today()
    score = 0
    signals: list[str] = []
    penalties: list[str] = []

    age_days = _post_age_days(postdate, today)
    if age_days is None:
        penalties.append("date_unknown")
    elif age_days <= 90:
        score += 5
        signals.append("recent_90d")
    elif age_days <= 180:
        score += 4
        signals.append("recent_180d")
    elif age_days <= 365:
        score += 3
        signals.append("recent_1y")
    elif age_days <= 730:
        score += 1
        signals.append("recent_2y")
    else:
        penalties.append("old_post")

    text = f"{title} {summary}"
    if area.strip() and not _area_matches(area, text):
        score -= 4
        penalties.append(f"area_missing:{area.strip()}")

    keyword_score = 0
    for keyword in _keywords(area, topic):
        if keyword in title:
            keyword_score += 2
            signals.append(f"title_match:{keyword}")
        elif keyword in summary:
            keyword_score += 1
            signals.append(f"summary_match:{keyword}")
    score += min(8, keyword_score)

    visit_score = 0
    for word in VISIT_REVIEW_WORDS:
        if word in text:
            visit_score += 1
            signals.append(f"visit:{word}")
    score += min(4, visit_score)

    open_score = 0
    for word in OPEN_STATUS_WORDS:
        if word in text:
            open_score += 1
            signals.append(f"open_hint:{word}")
    score += min(4, open_score)

    unlimited_matches = [word for word in UNLIMITED_REVIEW_WORDS if word in text]
    if unlimited_matches:
        requested = any(word in topic for word in ("무한리필", "무제한", "뷔페", "부페", "샤브샤브"))
        score += 3 if requested else 1
        signals.extend(f"unlimited:{word}" for word in unlimited_matches[:4])

    ad_matches = [word for word in AD_WORDS if word in text]
    if ad_matches:
        score -= 5
        penalties.extend(f"ad_like:{word}" for word in ad_matches)

    if not any(signal.startswith("visit:") for signal in signals):
        for word in ROUNDUP_WORDS:
            if word in text:
                score -= 2
                penalties.append(f"roundup:{word}")
                break

    return score, _dedupe(signals), _dedupe(penalties)


def _post_age_days(postdate: str, today: date) -> int | None:
    try:
        parsed = datetime.strptime(postdate, "%Y%m%d").date()
    except ValueError:
        return None
    return max(0, (today - parsed).days)


def _keywords(area: str, topic: str) -> list[str]:
    raw = [area.strip()]
    raw.extend(re.split(r"[\s,/]+", topic.strip()))
    cleaned: list[str] = []
    for token in raw:
        token = token.strip()
        if len(token) < 2:
            continue
        if token not in cleaned:
            cleaned.append(token)
    return cleaned


def _area_matches(area: str, text: str) -> bool:
    area = area.strip()
    if not area:
        return True
    variants = _area_variants(area)
    return any(variant and variant in text for variant in variants)


def _area_variants(area: str) -> list[str]:
    normalized = re.sub(r"\s+", " ", area).strip()
    if not normalized:
        return []
    variants = [normalized]
    tokens = normalized.split()
    for suffix in AREA_VARIANT_SUFFIXES:
        if normalized.endswith(suffix) and len(normalized) > len(suffix):
            variants.append(normalized[: -len(suffix)].strip())
    if len(tokens) > 1:
        for start in range(1, len(tokens)):
            variants.append(" ".join(tokens[start:]))
        if tokens[-1].endswith(AREA_LANDMARK_SUFFIXES):
            variants.append(tokens[0])
    return _dedupe([variant for variant in variants if len(variant) >= 2])


def _blog_matches_candidate(candidate: SearchCandidate, evidence: BlogEvidence) -> bool:
    evidence_text = f"{evidence.title} {evidence.summary}"
    return blog_text_matches_name(candidate.name, evidence_text)


def _match_local_candidates_to_blog(
    candidates: list[SearchCandidate],
    evidence_items: list[BlogEvidence],
    count: int,
) -> list[LocalBlogMatch]:
    sorted_evidence = sorted(evidence_items, key=lambda evidence: (-evidence.score, evidence.original_index))
    matches: list[LocalBlogMatch] = []
    for candidate_index, candidate in enumerate(candidates):
        evidence = [item for item in sorted_evidence if _blog_matches_candidate(candidate, item)]
        if evidence:
            metrics = _candidate_blog_metrics(candidate, evidence)
            matches.append(
                LocalBlogMatch(
                    candidate=candidate,
                    evidence=_select_candidate_evidence(evidence),
                    candidate_index=candidate_index,
                    matched_post_count=metrics["matched_post_count"],
                    recent_post_count=metrics["recent_post_count"],
                    unique_blogger_count=metrics["unique_blogger_count"],
                    title_match_count=metrics["title_match_count"],
                    summary_match_count=metrics["summary_match_count"],
                    snippet_signal_count=metrics["snippet_signal_count"],
                    ad_like_count=metrics["ad_like_count"],
                    stale_post_count=metrics["stale_post_count"],
                    aggregate_score=metrics["aggregate_score"],
                )
            )
    matches.sort(key=lambda match: (-match.aggregate_score, -match.best_score, match.candidate_index))
    return matches[:count]


def _candidate_blog_metrics(candidate: SearchCandidate, evidence_items: list[BlogEvidence]) -> dict[str, int]:
    normalized_name = normalize_match_text(candidate.name)
    matched_post_count = len(evidence_items)
    recent_post_count = sum(1 for evidence in evidence_items if _is_recent_evidence(evidence))
    unique_blogger_count = len(
        {
            normalize_match_text(evidence.blogger) or normalize_match_text(evidence.url)
            for evidence in evidence_items
        }
    )
    title_match_count = sum(
        1 for evidence in evidence_items if normalized_name and normalized_name in normalize_match_text(evidence.title)
    )
    summary_match_count = sum(
        1
        for evidence in evidence_items
        if normalized_name
        and normalized_name not in normalize_match_text(evidence.title)
        and normalized_name in normalize_match_text(evidence.summary)
    )
    snippet_signal_count = sum(_snippet_signal_count(evidence) for evidence in evidence_items)
    ad_like_count = sum(
        1 for evidence in evidence_items if any(penalty.startswith("ad_like:") for penalty in evidence.penalties)
    )
    stale_post_count = sum(1 for evidence in evidence_items if "old_post" in evidence.penalties)
    aggregate_score = (
        sum(evidence.score for evidence in evidence_items)
        + matched_post_count * 3
        + recent_post_count * 4
        + unique_blogger_count * 2
        + title_match_count * 4
        + summary_match_count
        + min(snippet_signal_count, 10)
        - ad_like_count * 6
        - stale_post_count * 4
    )
    return {
        "matched_post_count": matched_post_count,
        "recent_post_count": recent_post_count,
        "unique_blogger_count": unique_blogger_count,
        "title_match_count": title_match_count,
        "summary_match_count": summary_match_count,
        "snippet_signal_count": snippet_signal_count,
        "ad_like_count": ad_like_count,
        "stale_post_count": stale_post_count,
        "aggregate_score": aggregate_score,
    }


def _is_recent_evidence(evidence: BlogEvidence) -> bool:
    return any(signal in evidence.signals for signal in ("recent_90d", "recent_180d", "recent_1y"))


def _snippet_signal_count(evidence: BlogEvidence) -> int:
    signal_prefixes = ("visit:", "open_hint:", "unlimited:", "title_match:", "summary_match:")
    return sum(1 for signal in evidence.signals if signal.startswith(signal_prefixes))


def _select_candidate_evidence(evidence_items: list[BlogEvidence]) -> tuple[BlogEvidence, ...]:
    selected: list[BlogEvidence] = []
    for evidence in evidence_items:
        if not selected or evidence.score >= MIN_SUPPORTING_BLOG_SCORE:
            selected.append(evidence)
        if len(selected) >= BLOG_EVIDENCE_PER_CANDIDATE:
            break
    return tuple(selected)


def _dedupe_blog_evidence(evidence_items: list[BlogEvidence]) -> list[BlogEvidence]:
    seen_urls: set[str] = set()
    deduped: list[BlogEvidence] = []
    for evidence in evidence_items:
        if evidence.url in seen_urls:
            continue
        seen_urls.add(evidence.url)
        deduped.append(evidence)
    return deduped


def _targeted_blog_query(area: str, candidate: SearchCandidate) -> str:
    return " ".join(part for part in [area.strip(), candidate.name.strip(), "후기"] if part).strip()


def _secondary_blog_queries(area: str, topic: str, context_hint: str = "") -> list[str]:
    area = area.strip()
    topic = topic.strip()
    context_terms = _context_terms(context_hint)
    queries: list[str] = []
    if topic and topic != "맛집":
        queries.extend(
            [
                " ".join([area, topic, "후기"]).strip(),
                " ".join([area, topic, "내돈내산"]).strip(),
                " ".join([area, topic, "방문 후기"]).strip(),
            ]
        )
    else:
        queries.extend(
            [
                " ".join([area, "밥집 후기"]).strip(),
                " ".join([area, "식당 후기"]).strip(),
                " ".join([area, "점심 맛집 후기"]).strip(),
                " ".join([area, "내돈내산 맛집"]).strip(),
            ]
        )
    for term in context_terms:
        queries.append(" ".join([area, "맛집", term, "후기"]).strip())
    return _dedupe([query for query in queries if query])


def _needs_second_wave(matches: list[LocalBlogMatch], count: int) -> bool:
    if count <= 0:
        return False
    required = min(
        count,
        max(1, (count * SECOND_WAVE_MATCH_NUMERATOR + SECOND_WAVE_MATCH_DENOMINATOR - 1) // SECOND_WAVE_MATCH_DENOMINATOR),
    )
    return len(matches) < required


def _shorten_context_text(text: str, limit: int = CONTEXT_TEXT_LIMIT) -> str:
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + "..."


def _format_verified_matches(matches: list[LocalBlogMatch]) -> list[str]:
    if not matches:
        return []
    lines = [
        "Verified Kakao Local + Naver Blog evidence matches. "
        "Use only these Kakao-verified candidates as recommendation candidates:",
    ]
    for idx, match in enumerate(matches, start=1):
        candidate = match.candidate
        lines.append(
            f"{idx}. place={candidate.name} category={candidate.category} "
            f"address={_shorten_context_text(candidate.address, 80)} "
            f"aggregate_blog_score={match.aggregate_score} best_blog_score={match.best_score} "
            f"displayed_evidence_count={len(match.evidence)} matched_post_count={match.matched_post_count} "
            f"recent_post_count={match.recent_post_count} unique_blogger_count={match.unique_blogger_count} "
            f"title_exact_match_count={match.title_match_count} summary_match_count={match.summary_match_count} "
            f"snippet_signal_count={match.snippet_signal_count} ad_like_count={match.ad_like_count} "
            f"stale_post_count={match.stale_post_count}"
        )
        for blog_idx, evidence in enumerate(match.evidence, start=1):
            signals = ",".join(evidence.signals) if evidence.signals else "none"
            penalties = ",".join(evidence.penalties) if evidence.penalties else "none"
            lines.append(
                f"{idx}.{blog_idx} place={candidate.name} blog_score={evidence.score} "
                f"signals={signals} penalties={penalties} postdate={evidence.postdate} blog_url={evidence.url} "
                f"blog_title={_shorten_context_text(evidence.title)} "
                f"blog_summary={_shorten_context_text(evidence.summary)}"
            )
    return lines


class NaverBlogEvidenceProvider:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.quota = JsonQuotaGuard(
            settings.state_dir,
            settings.naver_daily_soft_limit,
            configured=self.configured,
            name="naver",
        )

    @property
    def configured(self) -> bool:
        return bool(self.settings.naver_client_id and self.settings.naver_client_secret)

    def collect_blog_evidence(
        self,
        query: str,
        area: str,
        topic: str,
        display: int,
        max_items: int,
    ) -> list[BlogEvidence]:
        blog = self.search("blog", query, display=display)
        items = blog.get("items") if isinstance(blog, dict) else []
        if not isinstance(items, list) or not items:
            return []
        evidence_items: list[BlogEvidence] = []
        for idx, item in enumerate(items[:max_items], start=1):
            if not isinstance(item, dict):
                continue
            link = str(item.get("link") or "").strip()
            if not self._allowed_blog_link(link):
                continue
            evidence_items.append(build_blog_evidence(item, area, topic, original_index=idx))
        if not evidence_items:
            return []
        evidence_items.sort(key=lambda evidence: (-evidence.score, evidence.original_index))
        return evidence_items

    def search(self, endpoint: str, query: str, display: int = 10, sort: str = "sim") -> dict[str, Any]:
        if endpoint != "blog":
            raise RuntimeError("Only Naver Blog search is supported by this provider")
        if not self.configured:
            raise NaverNotConfigured("NAVER_CLIENT_ID/NAVER_CLIENT_SECRET are not configured")
        self.quota.reserve(endpoint, query)
        params: dict[str, str | int] = {"query": query, "display": max(1, min(display, 100))}
        if sort:
            params["sort"] = sort
        url = f"https://openapi.naver.com/v1/search/{endpoint}.json?{urlencode(params)}"
        req = Request(url, method="GET")
        req.add_header("X-Naver-Client-Id", self.settings.naver_client_id)
        req.add_header("X-Naver-Client-Secret", self.settings.naver_client_secret)
        with urlopen(req, timeout=20) as resp:
            return json.loads(resp.read().decode("utf-8"))

    def _allowed_blog_link(self, url: str) -> bool:
        host = urlparse(url).netloc.lower()
        return any(host == domain or host.endswith("." + domain) for domain in self.settings.blog_allowed_domains)
