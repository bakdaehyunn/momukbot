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
from momukbot.core.models import SearchContext
from momukbot.storage.quota import JsonQuotaGuard, QuotaExceeded


class NaverNotConfigured(RuntimeError):
    pass


VISIT_REVIEW_WORDS = ("방문", "다녀왔", "먹고", "주문", "웨이팅", "내돈내산")
OPEN_STATUS_WORDS = ("24시", "새벽", "늦게", "영업시간", "라스트오더", "심야", "야간")
AD_WORDS = ("협찬", "제공받아", "체험단", "원고료", "광고")
ROUNDUP_WORDS = ("best", "BEST", "총정리", "모음", "리스트")


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


def format_blog_evidence(index: int, evidence: BlogEvidence) -> str:
    signals = ",".join(evidence.signals) if evidence.signals else "none"
    penalties = ",".join(evidence.penalties) if evidence.penalties else "none"
    return (
        f"{index}. score={evidence.score} signals={signals} penalties={penalties} "
        f"title={evidence.title} blogger={evidence.blogger} postdate={evidence.postdate} "
        f"url={evidence.url} summary={evidence.summary}"
    )


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


def _dedupe(items: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        if item in seen:
            continue
        seen.add(item)
        out.append(item)
    return out


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

    def build_context(self, area: str, topic: str, count: int = 30) -> SearchContext:
        if not self.configured:
            return SearchContext(configured=False, used_provider="naver")
        query_base = " ".join(part for part in [area, topic] if part).strip()
        if not query_base:
            return SearchContext(configured=True, used_provider="naver")
        parts: list[str] = []
        quota_blocked = False
        try:
            blog = self.search("blog", f"{query_base} 맛집 후기", display=min(30, max(10, count)))
            items = blog.get("items") if isinstance(blog, dict) else []
            if isinstance(items, list) and items:
                parts.append("Naver Blog Search results. Prefer these as review evidence:")
                evidence_items: list[BlogEvidence] = []
                for idx, item in enumerate(items[: min(30, count)], start=1):
                    if not isinstance(item, dict):
                        continue
                    link = str(item.get("link") or "").strip()
                    if not self._allowed_blog_link(link):
                        continue
                    evidence_items.append(build_blog_evidence(item, area, topic, original_index=idx))
                evidence_items.sort(key=lambda evidence: (-evidence.score, evidence.original_index))
                for idx, evidence in enumerate(evidence_items, start=1):
                    parts.append(format_blog_evidence(idx, evidence))
        except QuotaExceeded:
            quota_blocked = True
        except Exception as exc:
            parts.append(f"Naver blog search failed: {exc}")

        try:
            local = self.search("local", query_base, display=5, sort="comment")
            items = local.get("items") if isinstance(local, dict) else []
            if isinstance(items, list) and items:
                parts.extend(["", "Naver local search results as secondary place hints:"])
                for idx, item in enumerate(items[:5], start=1):
                    if not isinstance(item, dict):
                        continue
                    title = clean_html(str(item.get("title") or ""))
                    category = clean_html(str(item.get("category") or ""))
                    address = clean_html(str(item.get("roadAddress") or item.get("address") or ""))
                    link = str(item.get("link") or "").strip()
                    parts.append(f"{idx}. name={title} category={category} address={address} url={link}")
        except QuotaExceeded:
            quota_blocked = True
        except Exception as exc:
            parts.append(f"Naver local search failed: {exc}")

        return SearchContext(
            text="\n".join(parts).strip(),
            used_provider="naver",
            quota_blocked=quota_blocked,
            configured=True,
        )

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

    def _allowed_blog_link(self, url: str) -> bool:
        host = urlparse(url).netloc.lower()
        return any(host == domain or host.endswith("." + domain) for domain in self.settings.blog_allowed_domains)
