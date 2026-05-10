from __future__ import annotations

from datetime import datetime

from momukbot.agent.base import AgentProvider
from momukbot.config import Settings
from momukbot.core.formatter import (
    filter_preferred_links,
    format_recommendation_message,
    normalize_name,
)
from momukbot.core.json_utils import extract_json_object
from momukbot.core.models import ParsedRequest, RecommendationItem, RecommendationResult, SearchContext
from momukbot.core.parser import parse_request
from momukbot.core.prompts import recommendation_prompt
from momukbot.search.base import SearchProvider
from momukbot.storage.sqlite import RecommendationStore


class RecommendationService:
    def __init__(
        self,
        settings: Settings,
        agent: AgentProvider,
        search_provider: SearchProvider,
        store: RecommendationStore | None = None,
    ) -> None:
        self.settings = settings
        self.agent = agent
        self.search_provider = search_provider
        self.store = store

    def parse(self, text: str) -> ParsedRequest:
        return parse_request(text, default_count=self.settings.default_count)

    def handle_text(self, chat_id: str, text: str, dry_run: bool = False) -> str | None:
        parsed = self.parse(text)
        if parsed.intent == "unknown":
            return None
        if not parsed.area:
            return "지역을 못 찾았어요. 예: `서면에서 해장 국밥 추천해줘`처럼 지역을 포함해서 보내주세요."
        return self.recommend(chat_id=chat_id, request_text=text, parsed=parsed, dry_run=dry_run)

    def recommend(
        self,
        chat_id: str,
        request_text: str,
        parsed: ParsedRequest,
        dry_run: bool = False,
    ) -> str:
        parsed = ParsedRequest(
            intent=parsed.intent,
            area=parsed.area,
            topic=parsed.topic,
            meal_type=parsed.meal_type,
            budget=parsed.budget,
            occasion=parsed.occasion,
            count=max(1, min(30, parsed.count or self.settings.default_count)),
        )
        context_hint = ", ".join(
            item for item in [parsed.meal_type, parsed.budget, parsed.occasion] if item
        )
        search_context = self.search_provider.build_context(
            parsed.area,
            parsed.topic,
            parsed.count,
            context_hint=context_hint,
        )
        prompt = recommendation_prompt(parsed, datetime.now(), naver_context=search_context.text)
        if dry_run:
            return self._format_dry_run(parsed, search_context, prompt)

        raw = self.agent.generate(prompt)
        result = parse_recommendation(raw, self.settings.blog_allowed_domains)
        if self.store:
            self.store.add_result(
                chat_id=chat_id,
                request_text=request_text,
                area=parsed.area,
                topic=parsed.topic,
                search_keyword=result.search_keyword,
                raw_response=raw,
                items=result.items,
            )
        if not result.items and result.raw_text:
            return result.raw_text
        return format_recommendation_message(result.search_keyword, result.items)

    def _format_dry_run(self, parsed: ParsedRequest, search_context: SearchContext, prompt: str) -> str:
        lines = [
            "dry-run: 실제 AI 에이전트 호출은 하지 않았습니다.",
            f"area={parsed.area}",
            f"topic={parsed.topic or '(empty)'}",
            f"count={parsed.count}",
            f"search_provider={search_context.used_provider or '(none)'}",
            f"search_configured={search_context.configured}",
            f"quota_blocked={search_context.quota_blocked}",
            f"context_chars={len(search_context.text)}",
            "",
            "prompt_preview:",
            prompt[:3000],
        ]
        return "\n".join(lines)


def parse_recommendation(
    raw: str,
    allowed_domains: tuple[str, ...] = ("blog.naver.com",),
) -> RecommendationResult:
    data = extract_json_object(raw)
    if not data:
        return RecommendationResult(raw_text=raw)
    keyword = str(data.get("search_keyword") or "").strip()
    raw_items = data.get("items")
    items: list[RecommendationItem] = []
    seen: set[str] = set()
    if isinstance(raw_items, list):
        for raw_item in raw_items:
            if not isinstance(raw_item, dict):
                continue
            name = str(raw_item.get("name") or "").strip()
            key = normalize_name(name)
            if not name or not key or key in seen:
                continue
            seen.add(key)
            links = raw_item.get("links")
            clean_links: list[dict[str, str]] = []
            if isinstance(links, list):
                for link in links:
                    if not isinstance(link, dict):
                        continue
                    clean_links.append(
                        {
                            "label": str(link.get("label") or "링크").strip(),
                            "url": str(link.get("url") or "").strip(),
                        }
                    )
            items.append(
                RecommendationItem(
                    name=name,
                    category=str(raw_item.get("category") or "").strip(),
                    status_marker=str(raw_item.get("status_marker") or "영업시간 미확인").strip(),
                    reason=str(raw_item.get("reason") or "").strip(),
                    links=filter_preferred_links(clean_links, allowed_domains),
                )
            )
    return RecommendationResult(search_keyword=keyword, items=items, raw_text=raw, raw_json=data)
