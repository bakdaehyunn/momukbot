from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from html import unescape
from typing import Any
from urllib.parse import urlencode, urlparse
from urllib.request import Request, urlopen

from momukbot.config import Settings
from momukbot.core.matching import blog_text_matches_name, normalize_match_text
from momukbot.core.models import SearchCandidate, SearchContext
from momukbot.storage.quota import JsonQuotaGuard, QuotaExceeded


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
LOCAL_CANDIDATE_MULTIPLIER = 2
LOCAL_CANDIDATE_MAX = 60
LOCAL_CANDIDATE_EXPANDED_MULTIPLIER = 3
LOCAL_CANDIDATE_EXPANDED_MAX = 90
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

CAFE_INTENT_TERMS = ("카페", "커피", "커피집", "디저트", "베이커리", "빵")
GENERAL_EXCLUDED_NAME_WORDS = (
    "스타벅스",
    "이디야",
    "메가커피",
    "컴포즈커피",
    "투썸",
    "빽다방",
    "맥도날드",
    "버거킹",
    "롯데리아",
    "써브웨이",
    "서브웨이",
    "맘스터치",
    "KFC",
    "파파이스",
    "노브랜드버거",
)
GENERAL_EXCLUDED_CATEGORY_WORDS = (
    "카페",
    "커피",
    "디저트",
    "베이커리",
    "제과",
    "제빵",
    "도넛",
    "아이스크림",
    "패스트푸드",
    "브런치카페",
)
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

    @property
    def best_score(self) -> int:
        return self.evidence[0].score if self.evidence else 0


def clean_html(text: str) -> str:
    text = unescape(text or "")
    text = re.sub(r"</?b>", "", text)
    text = re.sub(r"<[^>]+>", "", text)
    return re.sub(r"\s+", " ", text).strip()


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


def _context_terms(context_hint: str) -> list[str]:
    terms: list[str] = []
    for token in re.split(r"[\s,/]+", context_hint.strip()):
        token = token.strip()
        if len(token) < 2:
            continue
        if token not in terms:
            terms.append(token)
    return terms[:3]


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


def _dedupe(items: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        if item in seen:
            continue
        seen.add(item)
        out.append(item)
    return out


def _local_candidate_queries(
    area: str,
    topic: str,
    count: int,
    context_hint: str = "",
    expanded: bool = False,
) -> list[str]:
    area = area.strip()
    topic = topic.strip()
    context_terms = _context_terms(context_hint)
    queries: list[str] = []
    if topic and topic != "맛집":
        queries.extend(
            [
                " ".join([area, topic]).strip(),
                " ".join([area, topic, "맛집"]).strip(),
                " ".join([area, "맛집", topic]).strip(),
            ]
        )
    else:
        queries.append(" ".join([area, "맛집"]).strip())

    for term in context_terms:
        queries.append(" ".join([area, "맛집", term]).strip())

    if topic and topic != "맛집" and not _allows_cafe_candidates(topic, context_hint):
        queries.extend(_same_intent_candidate_queries(area, topic))
    elif not _allows_cafe_candidates(topic, context_hint):
        queries.extend(
            [
                " ".join([area, "한식 맛집"]).strip(),
                " ".join([area, "고기 맛집"]).strip(),
                " ".join([area, "국수 맛집"]).strip(),
                " ".join([area, "일식 맛집"]).strip(),
                " ".join([area, "중식 맛집"]).strip(),
                " ".join([area, "양식 맛집"]).strip(),
                " ".join([area, "해장국"]).strip(),
                " ".join([area, "술집"]).strip(),
                " ".join([area, "점심 맛집"]).strip(),
                " ".join([area, "밥집"]).strip(),
                " ".join([area, "분식"]).strip(),
                " ".join([area, "족발"]).strip(),
                " ".join([area, "치킨"]).strip(),
                " ".join([area, "회식 맛집"]).strip(),
            ]
        )
        if expanded:
            queries.extend(_expanded_meal_candidate_queries(area))
    elif len(queries) < count:
        queries.extend(
            [
                " ".join([area, "카페"]).strip(),
                " ".join([area, "커피"]).strip(),
                " ".join([area, "디저트"]).strip(),
                " ".join([area, "베이커리"]).strip(),
            ]
        )
    return _dedupe([query for query in queries if query])


def _expanded_meal_candidate_queries(area: str) -> list[str]:
    return [
        " ".join([area, "백반"]).strip(),
        " ".join([area, "돈까스"]).strip(),
        " ".join([area, "돈카츠"]).strip(),
        " ".join([area, "찌개"]).strip(),
        " ".join([area, "냉면"]).strip(),
        " ".join([area, "칼국수"]).strip(),
        " ".join([area, "샤브샤브"]).strip(),
        " ".join([area, "초밥"]).strip(),
        " ".join([area, "덮밥"]).strip(),
        " ".join([area, "라멘"]).strip(),
        " ".join([area, "곱창"]).strip(),
        " ".join([area, "닭갈비"]).strip(),
        " ".join([area, "보쌈"]).strip(),
        " ".join([area, "식당"]).strip(),
    ]


def _same_intent_candidate_queries(area: str, topic: str) -> list[str]:
    text = topic.strip()
    groups: tuple[tuple[tuple[str, ...], tuple[str, ...]], ...] = (
        (
            ("국밥", "해장", "순대국", "순댓국", "감자탕", "설렁탕", "곰탕"),
            ("국밥", "순대국", "순댓국", "순대국밥", "돼지국밥", "해장국", "뼈해장국", "감자탕", "설렁탕", "곰탕"),
        ),
        (
            ("초밥", "스시", "일식", "라멘", "우동", "돈카츠", "돈까스"),
            ("일식", "초밥", "스시", "회전초밥", "사시미", "라멘", "우동", "돈카츠", "돈까스"),
        ),
        (
            ("중식", "마라", "마라탕", "짬뽕", "짜장", "양꼬치"),
            ("중식", "마라탕", "마라샹궈", "짬뽕", "짜장면", "양꼬치"),
        ),
        (
            ("고기", "삼겹", "갈비", "소고기", "돼지고기", "구이"),
            ("고기 맛집", "삼겹살", "목살", "갈비", "소고기", "돼지고기", "곱창", "구이"),
        ),
        (("술", "혼술", "술집", "이자카야", "포차", "맥주"), ("술집", "이자카야", "요리주점")),
        (("분식", "떡볶이", "김밥"), ("분식", "떡볶이", "김밥")),
        (("치킨", "닭"), ("치킨", "닭요리")),
        (("족발", "보쌈"), ("족발", "보쌈")),
        (("파스타", "양식", "스테이크", "피자"), ("양식", "파스타", "스테이크")),
    )
    for needles, expansions in groups:
        if any(needle in text for needle in needles):
            return [" ".join([area, expansion]).strip() for expansion in expansions]
    return []


def _allows_cafe_candidates(topic: str, context_hint: str = "") -> bool:
    text = " ".join([topic, context_hint])
    return any(term in text for term in CAFE_INTENT_TERMS)


def _local_candidate_target_count(count: int, expanded: bool = False) -> int:
    requested = max(1, count)
    if expanded:
        return min(
            LOCAL_CANDIDATE_EXPANDED_MAX,
            max(requested, requested * LOCAL_CANDIDATE_EXPANDED_MULTIPLIER),
        )
    return min(LOCAL_CANDIDATE_MAX, max(requested, requested * LOCAL_CANDIDATE_MULTIPLIER))


def _candidate_category(name: str, category: str) -> str:
    text = f"{name} {category}"
    if any(word in text for word in ("무한리필", "무제한", "뷔페", "부페", "샐러드바")):
        return "무한리필"
    if any(word in text for word in ("샤브샤브", "월남쌈", "편백찜")):
        return "샤브샤브"
    if any(word in text for word in ("카페", "커피", "디저트", "베이커리", "제과", "제빵", "빵")):
        return "카페"
    if any(word in text for word in ("국밥", "순대국", "순댓국")):
        return "국밥"
    if "감자탕" in text:
        return "감자탕"
    if any(word in text for word in ("해장국", "설렁탕", "곰탕")):
        return "해장국"
    if any(word in text for word in ("술집", "주점", "포차", "맥주", "이자카야", "와인")):
        return "술집"
    if any(word in text for word in ("일식", "초밥", "스시", "참치", "우동", "라멘", "돈카츠", "돈까스")):
        return "일식"
    if any(word in text for word in ("중식", "중국", "마라", "짬뽕", "짜장", "양꼬치")):
        return "중식"
    if any(
        word in text
        for word in (
            "한식",
            "고기",
            "갈비",
            "삼겹",
            "국수",
            "냉면",
            "백반",
            "분식",
            "족발",
            "보쌈",
            "곱창",
            "찌개",
            "구이",
            "닭",
            "치킨",
        )
    ):
        return "한식"
    return "기타"


def _is_excluded_general_candidate(candidate: SearchCandidate) -> bool:
    text = f"{candidate.name} {candidate.category} {candidate.raw_category}"
    if any(word in text for word in GENERAL_EXCLUDED_NAME_WORDS):
        return True
    return any(word in text for word in GENERAL_EXCLUDED_CATEGORY_WORDS)


def _candidate_key(candidate: SearchCandidate) -> str:
    return normalize_match_text(candidate.name)


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
            matches.append(
                LocalBlogMatch(
                    candidate=candidate,
                    evidence=_select_candidate_evidence(evidence),
                    candidate_index=candidate_index,
                )
            )
    matches.sort(key=lambda match: (-match.best_score, match.candidate_index))
    return matches[:count]


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
            f"best_blog_score={match.best_score} evidence_count={len(match.evidence)}"
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
