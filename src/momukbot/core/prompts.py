from __future__ import annotations

from datetime import datetime
from typing import Iterable

from .models import ParsedRequest


def recommendation_prompt(
    parsed: ParsedRequest,
    now: datetime,
    already_recommended: Iterable[str] = (),
    naver_context: str = "",
) -> str:
    excluded = "\n".join(f"- {name}" for name in already_recommended if name.strip()) or "(none)"
    food_preferences = parsed.topic or "맛집, 식당"
    context_hints = [item for item in [parsed.meal_type, parsed.budget, parsed.occasion] if item]
    if not context_hints:
        if now.hour >= 23 or now.hour < 5:
            context_hints = ["24시, 심야 영업 가능성"]
        elif now.hour >= 21:
            context_hints = ["야식, 늦게까지 하는 곳"]
    context = ", ".join(context_hints) or "(none)"

    return f"""You recommend Korean restaurants for a Telegram bot.

Current local time: {now.strftime('%Y-%m-%d %H:%M')}
Area: {parsed.area}
Primary target: food and places in {parsed.area}
Food/place preferences: {food_preferences}
Occasion/context hints: {context}
Need: {parsed.count} recommendations that are open now or likely open now.

Already recommended. Exclude these:
{excluded}

Naver API context:
{naver_context or "(not available; use your best judgement and clearly mark uncertainty)"}

Source strategy:
- Prefer Korean blog/review evidence from Naver Blog (`blog.naver.com`).
- Use map/place/official pages only as secondary evidence for existence or operating-hour hints.
- Do not use Tistory as blog/review evidence.
- Do not invent places or URLs.
- Use food/place preferences as the primary search axis.
- Treat occasion/context hints such as 혼술, 혼밥, 데이트, 회식 as ranking signals, not the main search keyword.
- If exact food/place candidates are few, broaden within the user's intent and nearby area.
- If the Naver API context says quota is blocked or unavailable, use your own web search capability to search Naver Blog only, if available.

Open status wording:
- "영업 확인됨" only when a source clearly supports current operating hours.
- "영업 가능성 높음" when 24-hour/night/open-late evidence strongly suggests it.
- "영업시간 미확인" when useful but exact current hours are not verified.

Return ONLY valid JSON. No markdown.
Schema:
{{
  "search_keyword": "the main Korean search keyword you used",
  "items": [
    {{
      "name": "place name",
      "category": "국밥|감자탕|해장국|술집|카페|일식|중식|한식|기타",
      "status_marker": "영업 확인됨|영업 가능성 높음|영업시간 미확인",
      "reason": "one short Korean sentence grounded mainly in blog/review evidence",
      "links": [
        {{"label": "블로그|리뷰|지도|공식", "url": "https://..."}}
      ]
    }}
  ]
}}

Constraints:
- Return exactly {parsed.count} items whenever there are enough credible candidates.
- If the Naver context has fewer exact-topic matches, broaden within the same area and nearby food intent before returning fewer items.
- Return fewer than {parsed.count} items only when adding more would require inventing places or URLs.
- Each item can have at most 2 links.
- Blog/review links must be from `blog.naver.com`.
- The formatter adds a Naver Map search link automatically.
"""
