from __future__ import annotations

import json
import re
from html import unescape
from typing import Any
from urllib.parse import urlencode, urlparse
from urllib.request import Request, urlopen

from momukbot.config import Settings
from momukbot.core.models import SearchContext
from momukbot.storage.quota import JsonQuotaGuard, QuotaExceeded


class NaverNotConfigured(RuntimeError):
    pass


def clean_html(text: str) -> str:
    text = unescape(text or "")
    text = re.sub(r"</?b>", "", text)
    text = re.sub(r"<[^>]+>", "", text)
    return re.sub(r"\s+", " ", text).strip()


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
                for idx, item in enumerate(items[: min(30, count)], start=1):
                    if not isinstance(item, dict):
                        continue
                    link = str(item.get("link") or "").strip()
                    if not self._allowed_blog_link(link):
                        continue
                    title = clean_html(str(item.get("title") or ""))
                    desc = clean_html(str(item.get("description") or ""))
                    postdate = str(item.get("postdate") or "").strip()
                    blogger = clean_html(str(item.get("bloggername") or ""))
                    parts.append(
                        f"{idx}. title={title} blogger={blogger} postdate={postdate} url={link} summary={desc}"
                    )
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
