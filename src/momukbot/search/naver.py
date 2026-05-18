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
from momukbot.core.models import SearchCandidate, SearchContext
from momukbot.storage.quota import JsonQuotaGuard, QuotaExceeded


class NaverNotConfigured(RuntimeError):
    pass


VISIT_REVIEW_WORDS = ("방문", "다녀왔", "먹고", "주문", "웨이팅", "내돈내산")
OPEN_STATUS_WORDS = ("24시", "새벽", "늦게", "영업시간", "라스트오더", "심야", "야간")
AD_WORDS = ("협찬", "제공받아", "체험단", "원고료", "광고")
ROUNDUP_WORDS = ("best", "BEST", "총정리", "모음", "리스트")
TARGETED_BLOG_SEARCH_LIMIT = 15
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
GENERIC_NAME_TOKENS = {
    "본점",
    "지점",
    "직영점",
    "역점",
    "점",
}


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


def _local_candidate_queries(area: str, topic: str, count: int, context_hint: str = "") -> list[str]:
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


def _same_intent_candidate_queries(area: str, topic: str) -> list[str]:
    text = topic.strip()
    groups: tuple[tuple[tuple[str, ...], tuple[str, ...]], ...] = (
        (("국밥", "해장", "순대국", "순댓국", "감자탕", "설렁탕", "곰탕"), ("국밥", "해장국", "순대국", "감자탕")),
        (("초밥", "스시", "일식", "라멘", "우동", "돈카츠", "돈까스"), ("일식", "초밥", "스시", "라멘")),
        (("중식", "마라", "마라탕", "짬뽕", "짜장", "양꼬치"), ("중식", "마라탕", "짬뽕")),
        (("고기", "삼겹", "갈비", "소고기", "돼지고기", "구이"), ("고기 맛집", "삼겹살", "갈비")),
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


def _candidate_from_local_item(item: dict[str, Any], query: str) -> SearchCandidate | None:
    title = clean_html(str(item.get("title") or ""))
    if not title:
        return None
    category = clean_html(str(item.get("category") or ""))
    address = clean_html(str(item.get("roadAddress") or item.get("address") or ""))
    link = str(item.get("link") or "").strip()
    return SearchCandidate(
        name=title,
        category=_candidate_category(title, category),
        raw_category=category,
        address=address,
        url=link,
        source="naver_local",
        query=query,
    )


def _candidate_category(name: str, category: str) -> str:
    text = f"{name} {category}"
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
    return _normalize_match_text(candidate.name)


def _normalize_match_text(text: str) -> str:
    return re.sub(r"[^0-9a-zA-Z가-힣]+", "", text).lower()


def _candidate_name_tokens(name: str) -> list[str]:
    tokens: list[str] = []
    for token in re.split(r"[\s,/()]+", name):
        normalized = _normalize_match_text(token)
        if normalized.endswith("점") and len(normalized) <= 4:
            continue
        if len(normalized) >= 3 and normalized not in GENERIC_NAME_TOKENS:
            tokens.append(normalized)
    return tokens


def _blog_matches_candidate(candidate: SearchCandidate, evidence: BlogEvidence) -> bool:
    name = _normalize_match_text(candidate.name)
    text = _normalize_match_text(f"{evidence.title} {evidence.summary}")
    if not name or not text:
        return False
    if name in text:
        return True
    tokens = _candidate_name_tokens(candidate.name)
    return bool(tokens) and any(token in text for token in tokens)


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
                    evidence=tuple(evidence[:2]),
                    candidate_index=candidate_index,
                )
            )
    matches.sort(key=lambda match: (-match.best_score, match.candidate_index))
    return matches[:count]


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


def _format_verified_matches(matches: list[LocalBlogMatch]) -> list[str]:
    if not matches:
        return []
    lines = [
        "Verified Naver Local + Naver Blog evidence matches. "
        "Use only these Local-verified candidates as recommendation candidates:",
    ]
    for idx, match in enumerate(matches, start=1):
        candidate = match.candidate
        lines.append(
            f"{idx}. place={candidate.name} category={candidate.category} "
            f"raw_category={candidate.raw_category} address={candidate.address} "
            f"map_url={candidate.url} local_query={candidate.query} best_blog_score={match.best_score}"
        )
        for blog_idx, evidence in enumerate(match.evidence, start=1):
            signals = ",".join(evidence.signals) if evidence.signals else "none"
            penalties = ",".join(evidence.penalties) if evidence.penalties else "none"
            lines.append(
                f"{idx}.{blog_idx} place={candidate.name} blog_score={evidence.score} "
                f"signals={signals} penalties={penalties} blogger={evidence.blogger} "
                f"postdate={evidence.postdate} blog_url={evidence.url} "
                f"blog_title={evidence.title} blog_summary={evidence.summary}"
            )
    return lines


class NaverSearchProvider:
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

    def build_context(
        self,
        area: str,
        topic: str,
        count: int = 30,
        context_hint: str = "",
    ) -> SearchContext:
        if not self.configured:
            return SearchContext(
                text=format_agent_naver_blog_fallback(
                    area,
                    topic,
                    "Naver API credentials are not configured",
                    context_hint=context_hint,
                ),
                configured=False,
                used_provider="naver",
                quota_blocked=True,
                evidence_available=False,
            )
        query_topic = "" if topic.strip() == "맛집" else topic
        query_base = " ".join(part for part in [area, query_topic] if part).strip()
        if not query_base:
            return SearchContext(configured=True, used_provider="naver", evidence_available=False)
        parts: list[str] = []
        candidates: list[SearchCandidate] = []
        matches: list[LocalBlogMatch] = []
        evidence_available = False
        quota_blocked = False
        try:
            candidates = self._build_local_candidates(
                area=area,
                topic=topic,
                count=count,
                context_hint=context_hint,
            )
        except QuotaExceeded:
            quota_blocked = True
        except Exception as exc:
            parts.append(f"Naver local search failed: {exc}")

        if candidates:
            try:
                evidence_items: list[BlogEvidence] = []
                primary_query = f"{query_base} 맛집 후기"
                blog_display = min(100, max(30, count * 2))
                evidence_items.extend(
                    self._collect_blog_evidence(
                        query=primary_query,
                        area=area,
                        topic=topic,
                        display=blog_display,
                        max_items=blog_display,
                    )
                )

                context_terms = _context_terms(context_hint)
                if context_terms:
                    context_query = " ".join([area, "맛집", *context_terms, "후기"]).strip()
                    if context_query != primary_query:
                        evidence_items.extend(
                            self._collect_blog_evidence(
                                query=context_query,
                                area=area,
                                topic=" ".join(part for part in [topic, *context_terms] if part),
                                display=min(30, max(10, count)),
                                max_items=min(30, max(10, count)),
                        )
                    )
                evidence_items = _dedupe_blog_evidence(evidence_items)
                matches = _match_local_candidates_to_blog(candidates, evidence_items, count)
                if len(matches) < count:
                    matched_keys = {_candidate_key(match.candidate) for match in matches}
                    unmatched = [
                        candidate
                        for candidate in candidates
                        if _candidate_key(candidate) not in matched_keys
                    ]
                    targeted_limit = min(
                        TARGETED_BLOG_SEARCH_LIMIT,
                        count - len(matches),
                        len(unmatched),
                    )
                    for candidate in unmatched[:targeted_limit]:
                        query = _targeted_blog_query(area, candidate)
                        evidence_items.extend(
                            self._collect_blog_evidence(
                                query=query,
                                area=area,
                                topic=topic,
                                display=5,
                                max_items=5,
                            )
                        )
                    evidence_items = _dedupe_blog_evidence(evidence_items)
                    matches = _match_local_candidates_to_blog(candidates, evidence_items, count)
                if matches:
                    evidence_available = True
                    if parts:
                        parts.append("")
                    parts.extend(_format_verified_matches(matches))
            except QuotaExceeded:
                quota_blocked = True
            except Exception as exc:
                parts.append(f"Naver blog search failed: {exc}")
        elif not quota_blocked and not parts:
            parts.append("Naver local search returned no usable restaurant candidates.")

        if quota_blocked and not parts:
            parts.append(
                format_agent_naver_blog_fallback(
                    area,
                    topic,
                    "Naver API quota is blocked",
                    context_hint=context_hint,
                )
            )
        elif quota_blocked:
            parts.extend(
                [
                    "",
                    format_agent_naver_blog_fallback(
                        area,
                        topic,
                        "Naver API quota is blocked",
                        context_hint=context_hint,
                    ),
                ]
            )

        return SearchContext(
            text="\n".join(parts).strip(),
            used_provider="naver",
            quota_blocked=quota_blocked,
            configured=True,
            evidence_available=evidence_available,
            candidates=[match.candidate for match in matches],
        )

    def _build_local_candidates(
        self,
        area: str,
        topic: str,
        count: int,
        context_hint: str = "",
    ) -> list[SearchCandidate]:
        allow_cafe = _allows_cafe_candidates(topic, context_hint)
        seen_candidates: set[str] = set()
        candidates: list[SearchCandidate] = []
        queries = _local_candidate_queries(area, topic, count, context_hint)
        max_queries = min(len(queries), max(1, (max(1, count) + 4) // 5))
        for query in queries[:max_queries]:
            local = self.search("local", query, display=5, sort="comment")
            items = local.get("items") if isinstance(local, dict) else []
            if not isinstance(items, list):
                continue
            for item in items:
                if not isinstance(item, dict):
                    continue
                candidate = _candidate_from_local_item(item, query)
                if candidate is None:
                    continue
                key = _candidate_key(candidate)
                if not key or key in seen_candidates:
                    continue
                if not allow_cafe and _is_excluded_general_candidate(candidate):
                    continue
                seen_candidates.add(key)
                candidates.append(candidate)
                if len(candidates) >= count:
                    return candidates
        return candidates

    def search(self, endpoint: str, query: str, display: int = 10, sort: str = "sim") -> dict[str, Any]:
        if not self.configured:
            raise NaverNotConfigured("NAVER_CLIENT_ID/NAVER_CLIENT_SECRET are not configured")
        self.quota.reserve(endpoint, query)
        params: dict[str, str | int] = {"query": query, "display": max(1, min(display, 100))}
        if sort:
            params["sort"] = sort
        if endpoint == "local":
            params["display"] = max(1, min(display, 5))
            params["start"] = 1
        url = f"https://openapi.naver.com/v1/search/{endpoint}.json?{urlencode(params)}"
        req = Request(url, method="GET")
        req.add_header("X-Naver-Client-Id", self.settings.naver_client_id)
        req.add_header("X-Naver-Client-Secret", self.settings.naver_client_secret)
        with urlopen(req, timeout=20) as resp:
            return json.loads(resp.read().decode("utf-8"))

    def _collect_blog_evidence(
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

    def _allowed_blog_link(self, url: str) -> bool:
        host = urlparse(url).netloc.lower()
        return any(host == domain or host.endswith("." + domain) for domain in self.settings.blog_allowed_domains)


def format_agent_naver_blog_fallback(
    area: str,
    topic: str,
    reason: str,
    context_hint: str = "",
) -> str:
    area = area.strip()
    topic = topic.strip()
    context_hint = context_hint.strip()
    base_query = " ".join(part for part in [area, "맛집", "후기"] if part).strip()
    lines = [
        reason + ".",
        "Do not use your own web search capability as a fallback.",
        f"Primary search query: site:blog.naver.com {base_query}",
    ]
    if topic and topic != "맛집":
        lines.append(f"Optional user hint: {topic}")
        lines.append(f"Optional refined query: site:blog.naver.com {area} 맛집 {topic} 후기".strip())
    if context_hint:
        lines.append(f"Optional context hint: {context_hint}")
        lines.append(f"Secondary context query: site:blog.naver.com {area} 맛집 {context_hint} 후기".strip())
    lines.extend(
        [
            "Do not use Tistory or non-Naver blog posts as blog/review evidence.",
            "If Naver API evidence is unavailable, stop instead of inventing recommendations.",
        ]
    )
    return "\n".join(lines)
