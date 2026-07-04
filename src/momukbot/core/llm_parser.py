from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any

from momukbot.agent.base import AgentProvider

from .json_utils import extract_json_object
from .models import ParsedRequest


LLM_REQUEST_PARSER_PROMPT = """\
You are momukbot's restaurant request parser.

Classify the user's Korean free-text message and extract fields for a restaurant recommendation pipeline.
Return only one JSON object. Do not recommend restaurants. Do not invent places.

Allowed JSON fields:
- intent: "restaurant_recommendation", "needs_location", or "unknown"
- area: named area/station/neighborhood only, e.g. "오목교역"; do not include menu, mood, time, or action words
- topic: requested food/category/menu, e.g. "곱창", "고기", "한식"; omit generic "맛집" if a more specific topic exists
- meal_type: one of "", "아침", "점심", "저녁", "야식"
- budget: one of "", "저렴", "비싼"
- occasion: short Korean occasion such as "혼밥", "혼술", "데이트", "회식", "술자리"
- count: integer between 1 and 30
- needs_location: boolean

Rules:
- If the user does not explicitly ask for a number of places, use count 30.
- If the user asks for "내 주변", "내 위치", "현재 위치", "여기 근처", "근처", or similar and no named area is present, use intent "needs_location", area "", needs_location true.
- If this is not a food/place recommendation request, use intent "unknown", area "", topic "", needs_location false.
- Separate area from food/category. Example: "오목교역 곱창 맛집 추천" => area "오목교역", topic "곱창".
- Separate descriptors from area. Example: "목동역 늦게까지 하는 고기집 추천" => area "목동역", topic "고기".

User message:
{text}
"""


AREA_SWALLOW_TERMS = (
    "곱창",
    "막창",
    "대창",
    "삼겹살",
    "목살",
    "갈비",
    "고기집",
    "고깃집",
    "파스타",
    "피자",
    "치킨",
    "라멘",
    "라면",
    "냉면",
    "족발",
    "보쌈",
    "마라탕",
    "탕수육",
    "양꼬치",
    "돈까스",
    "돈카츠",
    "곰탕",
    "설렁탕",
)
AREA_DESCRIPTOR_TERMS = (
    "늦게까지",
    "새벽",
    "24시간",
    "하는",
    "혼밥",
    "혼술",
    "데이트",
    "회식",
    "조용한",
    "가성비",
    "괜찮은",
    "좋은",
    "맛있는",
    "갈만한",
    "먹을만한",
    "오늘",
    "내일",
    "지금",
    "이번주",
    "이번 주",
    "주말",
    "2차",
    "한잔",
)
LOCATION_SUFFIXES = (
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
LOCATION_CONNECTOR_TERMS = ("부산", "서울", "대구", "인천", "광주", "대전", "울산", "제주")
GENERIC_TOPICS = ("", "맛집", "식당", "밥집")
WORK_INTENT_TERMS = (
    "데이터",
    "보고서",
    "회의록",
    "회의자료",
    "자료",
    "매출",
    "엑셀",
    "스프레드시트",
    "문서",
    "분석",
    "정리",
    "작성",
    "만들어",
    "준비",
)
FOODISH_TERMS = (
    "맛집",
    "밥",
    "식당",
    "술집",
    "야식",
    "혼밥",
    "혼술",
    "한잔",
    "뭐 먹",
    "뭐먹",
    "먹을",
    "먹지",
) + AREA_SWALLOW_TERMS


class LLMRequestParser:
    def __init__(self, agent: AgentProvider, default_count: int = 30, enabled: bool = True) -> None:
        self.agent = agent
        self.default_count = default_count
        self.enabled = enabled

    def parse(self, text: str, fallback: ParsedRequest) -> ParsedRequest:
        return self.parse_with_metadata(text, fallback).parsed

    def parse_with_metadata(self, text: str, fallback: ParsedRequest) -> "RequestParseResult":
        decision = route_decision(text, fallback, enabled=self.enabled)
        if not decision.use_llm:
            return RequestParseResult(parsed=fallback, source="rule", reason=decision.reason)
        raw = ""
        try:
            raw = self.agent.generate(LLM_REQUEST_PARSER_PROMPT.format(text=text.strip()))
        except Exception:
            return RequestParseResult(
                parsed=fallback,
                source="llm_fallback",
                reason="llm_exception",
                llm_used=True,
            )
        parsed = parse_llm_request(raw, default_count=self.default_count)
        if parsed is None:
            return RequestParseResult(
                parsed=fallback,
                source="llm_fallback",
                reason="malformed_or_unsupported_llm_output",
                llm_used=True,
                llm_raw_chars=len(raw),
            )
        parsed = _preserve_default_count_without_explicit_count(text, parsed, fallback)
        if parsed.intent == "unknown":
            return RequestParseResult(
                parsed=parsed,
                source="llm",
                reason="llm_classified_unknown",
                llm_used=True,
                llm_raw_chars=len(raw),
            )
        return RequestParseResult(
            parsed=parsed,
            source="llm",
            reason=decision.reason,
            llm_used=True,
            llm_raw_chars=len(raw),
        )


@dataclass(frozen=True)
class RouterDecision:
    use_llm: bool
    reason: str


@dataclass(frozen=True)
class RequestParseResult:
    parsed: ParsedRequest
    source: str
    reason: str
    llm_used: bool = False
    llm_raw_chars: int = 0


def route_decision(text: str, parsed: ParsedRequest, enabled: bool = True) -> RouterDecision:
    raw = text.strip()
    if not enabled:
        return RouterDecision(False, "disabled")
    if not raw:
        return RouterDecision(False, "blank")
    if raw.startswith("/"):
        return RouterDecision(False, "command")
    if parsed.intent == "unknown":
        if _looks_like_work_request(raw):
            return RouterDecision(False, "work_request")
        if _looks_foodish(raw):
            return RouterDecision(True, "unknown_foodish")
        return RouterDecision(False, "unknown_not_foodish")
    if parsed.intent not in {"start", "needs_location"}:
        return RouterDecision(False, f"unsupported_intent:{parsed.intent}")
    suspicious_reason = suspicious_area_reason(parsed.area, parsed.topic)
    if suspicious_reason:
        return RouterDecision(True, suspicious_reason)
    return RouterDecision(False, "rule_confident")


def suspicious_area_reason(area: str, topic: str) -> str:
    if not area:
        return ""
    if any(term in area for term in AREA_DESCRIPTOR_TERMS):
        return "suspicious_area_descriptor"
    if any(term in area for term in AREA_SWALLOW_TERMS):
        return "suspicious_area_food_term"
    tokens = area.split()
    if len(tokens) < 2:
        return ""
    if topic in GENERIC_TOPICS and any(_looks_non_location_area_tail(token) for token in tokens[1:]):
        return "suspicious_area_tail"
    return ""


def _looks_non_location_area_tail(token: str) -> bool:
    if token.endswith(LOCATION_SUFFIXES):
        return False
    return token not in LOCATION_CONNECTOR_TERMS


def parse_llm_request(raw: str, default_count: int = 30) -> ParsedRequest | None:
    data = extract_json_object(raw)
    if data is None:
        return None

    intent = _clean_text(data.get("intent")).lower()
    needs_location = _to_bool(data.get("needs_location"))
    if intent in {"unknown", "ignore", "unsupported", "not_food", "none"}:
        return ParsedRequest(intent="unknown", count=default_count)
    if needs_location or intent in {"needs_location", "location_required"}:
        return ParsedRequest(
            intent="needs_location",
            area="",
            topic=_clean_topic(data.get("topic")),
            meal_type=_clean_choice(data.get("meal_type"), ("", "아침", "점심", "저녁", "야식")),
            budget=_clean_choice(data.get("budget"), ("", "저렴", "비싼")),
            occasion=_clean_text(data.get("occasion")),
            count=_clean_count(data.get("count"), default_count),
        )
    if intent not in {"restaurant_recommendation", "restaurant", "food", "start"}:
        return None

    return ParsedRequest(
        intent="start",
        area=_clean_text(data.get("area")),
        topic=_clean_topic(data.get("topic")),
        meal_type=_clean_choice(data.get("meal_type"), ("", "아침", "점심", "저녁", "야식")),
        budget=_clean_choice(data.get("budget"), ("", "저렴", "비싼")),
        occasion=_clean_text(data.get("occasion")),
        count=_clean_count(data.get("count"), default_count),
    )


def _preserve_default_count_without_explicit_count(
    text: str,
    parsed: ParsedRequest,
    fallback: ParsedRequest,
) -> ParsedRequest:
    if _has_explicit_count(text):
        return parsed
    return ParsedRequest(
        intent=parsed.intent,
        area=parsed.area,
        topic=parsed.topic,
        meal_type=parsed.meal_type,
        budget=parsed.budget,
        occasion=parsed.occasion,
        count=fallback.count,
    )


def _has_explicit_count(text: str) -> bool:
    raw = text.strip()
    if re.search(r"\d+\s*(?:곳|개|군데|집|명|인분)", raw):
        return True
    return bool(re.search(r"(?:한|두|세|네|다섯|여섯|일곱|여덟|아홉|열)\s*(?:곳|개|군데|집)", raw))


def _looks_foodish(raw: str) -> bool:
    return any(term in raw for term in FOODISH_TERMS)


def _looks_like_work_request(raw: str) -> bool:
    return any(term in raw for term in WORK_INTENT_TERMS)


def _clean_text(value: Any, limit: int = 80) -> str:
    if value is None:
        return ""
    if isinstance(value, list):
        value = " ".join(str(item) for item in value if item)
    text = " ".join(str(value).split()).strip(" ,.!?`'\"")
    return text[:limit]


def _clean_topic(value: Any) -> str:
    topic = _clean_text(value, limit=100)
    parts = [part for part in topic.split() if part != "맛집"]
    return " ".join(parts) if parts else topic


def _clean_choice(value: Any, allowed: tuple[str, ...]) -> str:
    text = _clean_text(value)
    return text if text in allowed else ""


def _clean_count(value: Any, default_count: int) -> int:
    try:
        count = int(value)
    except (TypeError, ValueError):
        count = default_count
    return max(1, min(30, count))


def _to_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y", "on"}
    return bool(value)
