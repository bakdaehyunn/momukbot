from __future__ import annotations

from datetime import datetime
from typing import Iterable

from .models import ParsedRequest

CAFE_INTENT_TERMS = ("카페", "커피", "커피집", "디저트", "베이커리", "빵")


def recommendation_prompt(
    parsed: ParsedRequest,
    now: datetime,
    already_recommended: Iterable[str] = (),
    naver_context: str = "",
) -> str:
    excluded = "\n".join(f"- {name}" for name in already_recommended if name.strip()) or "(none)"
    food_preferences = parsed.topic or "맛집, 식당"
    cafe_allowed = _allows_cafe_results(parsed)
    general_food_request = not parsed.topic or parsed.topic == "맛집"
    category_choices = (
        "국밥|감자탕|해장국|술집|카페|일식|중식|한식|기타"
        if cafe_allowed
        else "국밥|감자탕|해장국|술집|일식|중식|한식|기타"
    )
    venue_scope = (
        "- The user explicitly asked for cafe/coffee/dessert/bakery, so cafe-like places are allowed."
        if cafe_allowed
        else "\n".join(
            [
                '- General "맛집" means meal-serving restaurants, not cafes or coffee shops.',
                "- Exclude cafes, coffee chains, dessert-only shops, bakery-only shops, and large fast-food chains unless the user explicitly asks for them.",
                "- Excluded examples for general 맛집: 스타벅스, 이디야, 메가커피, 컴포즈커피, 투썸플레이스, 빽다방, 맥도날드, 버거킹, 롯데리아.",
                "- When filling all requested slots, broaden across meal-serving Korean/Japanese/Chinese/Western/noodle/meat/pub restaurants instead of using cafe or coffee candidates.",
                "- Do not use secondary Naver local hints as filler if their name or category is cafe, coffee, dessert, bakery, or large fast-food chain.",
            ]
        )
    )
    context_hints = [item for item in [parsed.meal_type, parsed.budget, parsed.occasion] if item]
    fill_rule = (
        "- If exact-topic matches are limited, fill the remaining slots from nearby food intents and secondary place hints in the provided Naver context."
        if general_food_request
        else "- If exact-topic matches are limited, stay within the same food intent; do not fill with unrelated restaurant types."
    )
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
Need: {parsed.count} recommendations. Use open-status markers only when supported by the provided context.

Already recommended. Exclude these:
{excluded}

Naver API context:
{naver_context or "(not available; do not use your own web search fallback)"}

Source strategy:
- Prefer Korean blog/review evidence from Naver Blog (`blog.naver.com`).
- Use map/place/official pages only as secondary evidence for existence or operating-hour hints.
- Do not return non-Naver-Blog URLs in `links`; the formatter adds a Naver Map link automatically.
- Do not use Tistory as blog/review evidence.
- Do not invent places or URLs.
- Use food/place preferences as the primary search axis.
- Treat occasion/context hints such as 혼술, 혼밥, 데이트, 회식 as ranking signals, not the main search keyword.
- If exact food/place candidates are few, broaden only within the user's intent and nearby area.
- If the Naver API context contains a deterministic local candidate roster, treat that roster as the canonical candidate pool and return exactly the requested count from it before using other context.
- If the Naver API context above contains candidate evidence, do not perform any additional web searches.
- Do not use your own web search when the Naver API context is empty, quota-blocked, or unavailable.
- Every returned item must include at least one Naver Blog URL copied from the provided Naver API context.
- The Naver Blog URL for each item must be from evidence whose title or summary names that exact place. Do not attach another restaurant's blog URL to an item.

Venue scope:
{venue_scope}

Open status wording:
- "영업 확인됨" only when a source clearly supports current operating hours.
- "영업 가능성 높음" when 24-hour/night/open-late evidence strongly suggests it.
- "영업시간 미확인" when useful but exact current hours are not verified.
- Do not search again just to verify operating hours. If the provided context does not clearly verify hours, use "영업시간 미확인".

Return ONLY valid JSON. No markdown.
Schema:
{{
  "search_keyword": "the main Korean search keyword you used",
  "items": [
    {{
      "name": "place name",
      "category": "{category_choices}",
      "status_marker": "영업 확인됨|영업 가능성 높음|영업시간 미확인",
      "reason": "one short Korean sentence grounded mainly in blog/review evidence",
      "links": [
        {{"label": "네이버 블로그", "url": "https://blog.naver.com/..."}}
      ]
    }}
  ]
}}

Constraints:
- Return exactly {parsed.count} items.
- Do not return fewer items because operating hours are uncertain. Use "영업시간 미확인" instead.
{fill_rule}
- Return fewer than {parsed.count} items only if the provided Naver context has fewer than {parsed.count} distinct named place candidates.
- Each item can have at most 2 links.
- Links must be Naver Blog URLs from `blog.naver.com`; omit links when no Naver Blog URL is available.
- Items without a Naver Blog URL from the provided context are rejected by the service.
- Items whose Naver Blog URL does not mention that item's place name in the provided title/summary are rejected by the service.
- The formatter adds a Naver Map search link automatically.
"""


def _allows_cafe_results(parsed: ParsedRequest) -> bool:
    text = " ".join([parsed.topic, parsed.meal_type, parsed.budget, parsed.occasion])
    return any(term in text for term in CAFE_INTENT_TERMS)


def complete_recommendation_prompt(
    parsed: ParsedRequest,
    naver_context: str,
    current_raw: str,
    current_count: int,
) -> str:
    cafe_allowed = _allows_cafe_results(parsed)
    category_choices = (
        "국밥|감자탕|해장국|술집|카페|일식|중식|한식|기타"
        if cafe_allowed
        else "국밥|감자탕|해장국|술집|일식|중식|한식|기타"
    )
    venue_rule = (
        "Cafe/coffee/dessert/bakery places are allowed because the user explicitly asked for them."
        if cafe_allowed
        else (
            'General "맛집" means meal-serving restaurants. Exclude cafes, coffee chains, '
            "dessert-only shops, bakery-only shops, and large fast-food chains."
        )
    )
    fill_rule = (
        "For a general 맛집 request, missing slots may be filled from nearby meal-serving food intents in the provided context."
        if not parsed.topic or parsed.topic == "맛집"
        else "For a specific food request, fill missing slots only within the same food intent; do not use unrelated restaurant types."
    )
    return f"""The previous recommendation JSON returned only {current_count} items, but exactly {parsed.count} items are required.

Return a complete replacement JSON with exactly {parsed.count} items.
Keep acceptable existing items from the previous JSON, then fill missing slots using only the provided Naver API context.
Do not perform any additional web searches.
Do not return fewer items because operating hours are uncertain. Use "영업시간 미확인" instead.
Do not return non-Naver-Blog URLs in `links`; the formatter adds a Naver Map link automatically.
Every returned item must include at least one Naver Blog URL copied from the provided Naver API context.
Each item's Naver Blog URL must be from evidence whose title or summary names that exact place.
{fill_rule}
{venue_rule}

Area: {parsed.area}
Food/place preferences: {parsed.topic or "맛집, 식당"}
Allowed category values: {category_choices}

Naver API context:
{naver_context or "(not available)"}

Previous JSON:
{current_raw}

Return ONLY valid JSON. No markdown.
Schema:
{{
  "search_keyword": "the main Korean search keyword you used",
  "items": [
    {{
      "name": "place name",
      "category": "{category_choices}",
      "status_marker": "영업 확인됨|영업 가능성 높음|영업시간 미확인",
      "reason": "one short Korean sentence grounded mainly in the provided context",
      "links": [
        {{"label": "네이버 블로그", "url": "https://blog.naver.com/..."}}
      ]
    }}
  ]
}}
"""
