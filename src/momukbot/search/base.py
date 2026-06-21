from __future__ import annotations

from typing import Protocol

from momukbot.core.models import RequestLocation, SearchContext


class SearchProvider(Protocol):
    def build_context(
        self,
        area: str,
        topic: str,
        count: int = 30,
        context_hint: str = "",
        location: RequestLocation | None = None,
    ) -> SearchContext:
        """Return source context for a restaurant recommendation."""
