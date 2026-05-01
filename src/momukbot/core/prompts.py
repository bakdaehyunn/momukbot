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
    hints = ", ".join(
        item for item in [parsed.topic, parsed.meal_type, parsed.budget, parsed.occasion] if item
    )
    if not hints:
        if now.hour >= 23 or now.hour < 5:
            hints = "24시, 해장국, 국밥, 심야"
        elif now.hour >= 21:
            hints = "야식, 술집, 늦게까지 하는 곳"
        else:
            hints = "맛집, 식당"

    return f"""You recommend Korean restaurants for a Telegram bot.

Current local time: {now.strftime('%Y-%m-%d %H:%M')}
Area: {parsed.area}
User hints: {hints}
Need: up to {parsed.count} recommendations that are open now or likely open now.

Already recommended. Exclude these:
{excluded}

Naver API context:
{naver_context or "(not available; use your best judgement and clearly mark uncertainty)"}

Source strategy:
- Prefer Korean blog/review evidence from Naver Blog (`blog.naver.com`) and Tistory (`tistory.com`).
- Use map/place/official pages only as secondary evidence for existence or operating-hour hints.
- Do not invent places or URLs.
- If exact-topic candidates are few, broaden within the user's intent and nearby area.

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
- Return at most {parsed.count} items.
- Prefer returning 20-30 items for broad requests.
- Each item can have at most 2 links.
- Blog/review links must be from `blog.naver.com` or `tistory.com`.
- The formatter adds a Naver Map search link automatically.
"""
