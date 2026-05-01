from __future__ import annotations

from momukbot.agent.base import AgentProvider
from momukbot.agent.codex_cli import CodexCliAgent
from momukbot.config import Settings
from momukbot.core.service import RecommendationService
from momukbot.search.naver import NaverSearchProvider
from momukbot.storage.sqlite import RecommendationStore


def build_agent(settings: Settings) -> AgentProvider:
    if settings.agent_provider != "codex_cli":
        raise RuntimeError(f"unsupported AGENT_PROVIDER={settings.agent_provider!r}")
    return CodexCliAgent(settings)


def build_service(settings: Settings, persist: bool = True) -> RecommendationService:
    store = RecommendationStore(settings.state_dir) if persist else None
    return RecommendationService(
        settings=settings,
        agent=build_agent(settings),
        search_provider=NaverSearchProvider(settings),
        store=store,
    )
