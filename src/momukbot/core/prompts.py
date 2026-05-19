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
    request_text: str = "",
) -> str:
    excluded = "\n".join(f"- {name}" for name in already_recommended if name.strip()) or "(none)"
    food_preferences = parsed.topic or "맛집, 식당"
    cafe_allowed = _allows_cafe_results(parsed)
    general_food_request = not parsed.topic or parsed.topic == "맛집"
    category_choices = (
        "국밥|감자탕|해장국|술집|카페|일식|중식|한식|무한리필|샤브샤브|기타"
        if cafe_allowed
        else "국밥|감자탕|해장국|술집|일식|중식|한식|무한리필|샤브샤브|기타"
    )
    venue_scope = (
        "- The user explicitly asked for cafe/coffee/dessert/bakery, so cafe-like places are allowed."
        if cafe_allowed
        else "\n".join(
            [
                '- General "맛집" means meal-serving restaurants, not cafes or coffee shops.',
                "- Exclude cafes, coffee chains, dessert-only shops, bakery-only shops, and large fast-food chains unless the user explicitly asks for them.",
                "- Excluded examples for general 맛집: 스타벅스, 이디야, 메가커피, 컴포즈커피, 투썸플레이스, 빽다방, 맥도날드, 버거킹, 롯데리아.",
            ]
        )
    )
    context_hints = [item for item in [parsed.meal_type, parsed.budget, parsed.occasion] if item]
    fill_rule = (
        "- If exact-topic matches are limited, include only nearby meal-serving restaurants with matching Naver Blog evidence from the provided context."
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
Original user request: {request_text.strip() or "(not provided)"}
Area: {parsed.area}
Primary target: food and places in {parsed.area}
Food/place preferences: {food_preferences}
Occasion/context hints: {context}
Need: up to {parsed.count} recommendations. Use open-status markers only when supported by the provided context.

Already recommended. Exclude these:
{excluded}

Naver API context:
{naver_context or "(not available; do not use your own web search fallback)"}

Source strategy:
- Prefer Korean blog/review evidence from Naver Blog (`blog.naver.com`).
- Use map/place/official pages only as secondary evidence for existence or operating-hour hints, not as recommendation evidence.
- When the context lists "Verified Naver Local + Naver Blog evidence matches", use those Local-verified candidate names as the candidate pool.
- The code has already scored, filtered, and ordered the Local-verified candidates.
- You may reorder verified candidates to fit the original user request, such as 혼밥, 혼술, 데이트, 회식, budget, noise level, or late-night needs.
- Your main job is request-aware ranking and concise Korean explanation, not place discovery.
- Score every returned item against the original user request. The service uses these structured scores to rank code-verified candidates.
- Use `intent_fit` for overall request fit, `meal_fit` for whether it is a real meal-serving restaurant for this request, and `occasion_fit` for occasion/context fit.
- Use `risk_flags` for compact internal caveats such as `large_chain`, `cafe_like`, `dessert_only`, `fast_food`, `menu_unclear`, `occasion_mismatch`, `unlimited_refill_solo_mismatch`, or `weak_fit`.
- Do not expose numeric fit scores or risk flag names in user-facing Korean text.
- Write `top_summary` as a practical guide to the first three returned places, naming when each one is useful.
- Do not write source-checking statements as the main user-facing reason. Prefer why the user would choose the place: menu fit, solo-friendliness, atmosphere, value, meal type, late-night usefulness, or occasion fit.
- `decision_criteria` and `top_summary` must use user-facing choice criteria only. Do not mention internal validation criteria such as Naver, blog evidence, Local, verified candidates, recent reviews, or source matching.
- Do not replace listed candidates with your own alternatives.
- Naver Local confirms place existence/category/address only; a place still needs matching Naver Blog evidence to be recommended.
- Do not return non-Naver-Blog URLs in `links`; the formatter adds a Naver Map link automatically.
- Do not use Tistory as blog/review evidence.
- Do not invent places or URLs.
- Use food/place preferences as the primary search axis.
- Treat occasion/context hints such as 혼술, 혼밥, 데이트, 회식 as ranking signals, not the main search keyword.
- Treat 무한리필, 무제한, 뷔페/부페, 샤브샤브, 샐러드바, 리필, 월남쌈, and 편백찜 as unlimited-refill signals.
- If the user asks for 무한리필/무제한/뷔페/샤브샤브/value, rank unlimited-refill candidates higher when the provided evidence supports that signal.
- 혼밥 요청에는 무한리필 candidates can be weaker fits. Keep them only when useful, add `unlimited_refill_solo_mismatch` when appropriate, and explain that they may be better for two or more people.
- Do not invent exact prices, time limits, or refill rules. If evidence is unclear, say 가격이나 시간제한은 방문 전 확인이 필요합니다.
- If exact food/place candidates are few, broaden only within the user's intent and nearby area, and only when matching Naver Blog evidence exists.
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
  "decision_criteria": ["2-5 short Korean user-facing criteria, e.g. 혼밥하기 편한 메뉴, 혼자 들어가기 부담 적은 곳"],
  "top_summary": "one short Korean user-facing guide to the first three places; name each of the top three when there are at least three items; do not mention Naver, blog evidence, Local, verification, or source matching",
  "items": [
    {{
      "name": "place name",
      "category": "{category_choices}",
      "status_marker": "영업 확인됨|영업 가능성 높음|영업시간 미확인",
      "intent_fit": 0,
      "meal_fit": 0,
      "occasion_fit": 0,
      "risk_flags": [],
      "fit_tags": ["1-4 short Korean tags such as 혼밥, 조용함, 가성비, 무한리필, 늦은시간"],
      "tradeoff": "one short Korean caveat when useful; empty string if none",
      "reason": "one short Korean sentence explaining why the user would choose this place; avoid source-checking phrasing",
      "links": [
        {{"label": "네이버 블로그", "url": "https://blog.naver.com/..."}}
      ]
    }}
  ]
}}

Constraints:
- Return up to {parsed.count} items.
- If the verified candidate pool has {parsed.count} or fewer items, return every verified candidate.
- Do not return fewer items because operating hours are uncertain. Use "영업시간 미확인" instead.
{fill_rule}
- Return fewer than {parsed.count} items when the provided Naver Blog evidence does not confirm enough distinct named place candidates.
- Each item can have at most 2 links.
- Links must be Naver Blog URLs from `blog.naver.com`; omit links when no Naver Blog URL is available.
- Items without a Naver Blog URL from the provided context are rejected by the service.
- Items whose Naver Blog URL does not mention that item's place name in the provided title/summary are rejected by the service.
- Fit scores must be integers from 0 to 5. Higher means the item is a better match for the original request.
- For general 맛집 requests, set low `meal_fit` and add a risk flag for cafes, dessert-only shops, coffee chains, and fast-food chains unless explicitly requested.
- For unlimited-refill places, use tags such as 무한리필, 샤브샤브, 가성비, 모임 when supported by the provided evidence.
- Use `decision_criteria`, `fit_tags`, and `tradeoff` to show your reasoning compactly without inventing facts.
- `reason` should not be "후기가 확인됩니다" or "근거가 있습니다" by itself. Turn evidence into a user-facing reason such as "혼자 먹기 쉬운 단품 메뉴라 점심 혼밥에 무난합니다."
- The formatter adds a Naver Map search link automatically.
"""


def _allows_cafe_results(parsed: ParsedRequest) -> bool:
    text = " ".join([parsed.topic, parsed.meal_type, parsed.budget, parsed.occasion])
    return any(term in text for term in CAFE_INTENT_TERMS)
