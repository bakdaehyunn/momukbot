from __future__ import annotations

import logging
import time
import uuid
from datetime import datetime
from typing import Protocol

from momukbot.agent.base import AgentProvider
from momukbot.config import Settings
from momukbot.core.formatter import format_recommendation_message
from momukbot.core.llm_parser import LLMRequestParser, RequestParseResult
from momukbot.core.models import (
    ParsedRequest,
    RecommendationItem,
    RequestLocation,
    SearchContext,
)
from momukbot.core.observability import (
    RecommendationEvent,
    RecommendationOutcome,
    classify_evidence_unavailable,
)
from momukbot.core.parser import parse_request
from momukbot.core.reconciliation import (
    attach_map_candidates as _attach_map_candidates,
    average_confidence as _average_confidence,
    confirmed_blog_evidence as _confirmed_blog_evidence,
    diversity_group_count as _diversity_group_count,
    format_drop_reasons as _format_drop_reasons,
    multi_blog_candidate_count as _multi_blog_candidate_count,
    reconcile_result_items as _reconcile_result_items,
)
from momukbot.core.prompts import recommendation_prompt
from momukbot.core.result_parser import parse_recommendation
from momukbot.search.base import SearchProvider


class RecommendationEventRecorder(Protocol):
    def record(self, event: RecommendationEvent) -> None:
        """Persist one structured recommendation event."""


class RecommendationResultStore(Protocol):
    def add_result(
        self,
        chat_id: str,
        request_text: str,
        area: str,
        topic: str,
        search_keyword: str,
        raw_response: str,
        items: list[RecommendationItem],
    ) -> None:
        """Persist one recommendation result."""


class RecommendationService:
    def __init__(
        self,
        settings: Settings,
        agent: AgentProvider,
        search_provider: SearchProvider,
        store: RecommendationResultStore | None = None,
        logger: logging.Logger | None = None,
        request_parser: LLMRequestParser | None = None,
        event_recorder: RecommendationEventRecorder | None = None,
    ) -> None:
        self.settings = settings
        self.agent = agent
        self.search_provider = search_provider
        self.store = store
        self.logger = logger or logging.getLogger("momukbot.telegram")
        self.event_recorder = event_recorder
        self.request_parser = request_parser or LLMRequestParser(
            agent,
            settings.default_count,
            enabled=settings.llm_request_parser_enabled,
        )

    def parse(self, text: str) -> ParsedRequest:
        return self.parse_with_metadata(text).parsed

    def parse_with_metadata(self, text: str) -> RequestParseResult:
        parsed = parse_request(text, default_count=self.settings.default_count)
        return self.request_parser.parse_with_metadata(text, parsed)

    def handle_text(self, chat_id: str, text: str, dry_run: bool = False) -> str | None:
        request_id = str(uuid.uuid4())
        total_start = time.monotonic()
        stage_start = time.monotonic()
        parse_result = self.parse_with_metadata(text)
        parsed = parse_result.parsed
        parse_elapsed = time.monotonic() - stage_start
        self._log_stage(
            chat_id,
            "parse",
            parse_elapsed,
            intent=parsed.intent,
            has_area=bool(parsed.area),
            count=parsed.count,
            parse_source=parse_result.source,
            parse_reason=parse_result.reason,
            llm_parser_used=parse_result.llm_used,
            llm_parser_raw_chars=parse_result.llm_raw_chars,
        )
        if parsed.intent == "unknown":
            self._record_event(
                self._event_for_request(
                    request_id,
                    chat_id,
                    RecommendationOutcome.IGNORED,
                    parsed,
                    parse_result=parse_result,
                    total_elapsed=time.monotonic() - total_start,
                    stage_ms={"parse": _ms(parse_elapsed)},
                    failure_reason=RecommendationOutcome.IGNORED,
                    dry_run=dry_run,
                )
            )
            self._log_stage(chat_id, "total", time.monotonic() - total_start, result="ignored")
            return None
        if parsed.intent == "needs_location":
            self._record_event(
                self._event_for_request(
                    request_id,
                    chat_id,
                    RecommendationOutcome.LOCATION_REQUIRED,
                    parsed,
                    parse_result=parse_result,
                    total_elapsed=time.monotonic() - total_start,
                    stage_ms={"parse": _ms(parse_elapsed)},
                    failure_reason=RecommendationOutcome.LOCATION_REQUIRED,
                    dry_run=dry_run,
                )
            )
            self._log_stage(chat_id, "total", time.monotonic() - total_start, result="location_required")
            return location_request_message()
        if not parsed.area:
            self._record_event(
                self._event_for_request(
                    request_id,
                    chat_id,
                    RecommendationOutcome.MISSING_AREA,
                    parsed,
                    parse_result=parse_result,
                    total_elapsed=time.monotonic() - total_start,
                    stage_ms={"parse": _ms(parse_elapsed)},
                    failure_reason=RecommendationOutcome.MISSING_AREA,
                    dry_run=dry_run,
                )
            )
            self._log_stage(chat_id, "total", time.monotonic() - total_start, result="missing_area")
            return "지역을 못 찾았어요. 예: `서면에서 해장 국밥 추천해줘`처럼 지역을 포함해서 보내주세요."
        return self.recommend(
            chat_id=chat_id,
            request_text=text,
            parsed=parsed,
            dry_run=dry_run,
            started_at=total_start,
            request_id=request_id,
            parse_result=parse_result,
            initial_stage_ms={"parse": _ms(parse_elapsed)},
        )

    def handle_location(
        self,
        chat_id: str,
        latitude: float,
        longitude: float,
        text: str = "내 주변 맛집 추천",
        dry_run: bool = False,
    ) -> str | None:
        request_id = str(uuid.uuid4())
        total_start = time.monotonic()
        stage_start = time.monotonic()
        parse_result = self.parse_with_metadata(text)
        parsed = parse_result.parsed
        if parsed.intent == "unknown":
            parsed = ParsedRequest(intent="start", topic="맛집", count=self.settings.default_count)
        parsed = ParsedRequest(
            intent="start",
            area=parsed.area or "현재 위치",
            topic=parsed.topic or "맛집",
            meal_type=parsed.meal_type,
            budget=parsed.budget,
            occasion=parsed.occasion,
            count=parsed.count,
        )
        parse_result = RequestParseResult(
            parsed=parsed,
            source=parse_result.source or "location",
            reason=parse_result.reason or "location_provided",
            llm_used=parse_result.llm_used,
            llm_raw_chars=parse_result.llm_raw_chars,
        )
        parse_elapsed = time.monotonic() - stage_start
        self._log_stage(
            chat_id,
            "parse",
            parse_elapsed,
            intent="location",
            has_area=bool(parsed.area),
            count=parsed.count,
            location_provided=True,
        )
        return self.recommend(
            chat_id=chat_id,
            request_text=text,
            parsed=parsed,
            dry_run=dry_run,
            started_at=total_start,
            location=RequestLocation(latitude=latitude, longitude=longitude),
            request_id=request_id,
            parse_result=parse_result,
            initial_stage_ms={"parse": _ms(parse_elapsed)},
        )

    def recommend(
        self,
        chat_id: str,
        request_text: str,
        parsed: ParsedRequest,
        dry_run: bool = False,
        started_at: float | None = None,
        location: RequestLocation | None = None,
        request_id: str | None = None,
        parse_result: RequestParseResult | None = None,
        initial_stage_ms: dict[str, int] | None = None,
    ) -> str:
        request_id = request_id or str(uuid.uuid4())
        total_start = started_at or time.monotonic()
        stage_ms = dict(initial_stage_ms or {})
        stage_start = time.monotonic()
        parsed_area = parsed.area
        if location and location.label:
            parsed_area = location.label
        parsed = ParsedRequest(
            intent=parsed.intent,
            area=parsed_area,
            topic=parsed.topic,
            meal_type=parsed.meal_type,
            budget=parsed.budget,
            occasion=parsed.occasion,
            count=max(1, min(30, parsed.count or self.settings.default_count)),
        )
        if parse_result is None:
            parse_result = RequestParseResult(parsed=parsed, source="manual", reason="manual_recommend")
        normalize_elapsed = time.monotonic() - stage_start
        stage_ms["normalize"] = _ms(normalize_elapsed)
        self._log_stage(
            chat_id,
            "normalize",
            normalize_elapsed,
            count=parsed.count,
            has_topic=bool(parsed.topic),
        )
        context_hint = ", ".join(
            item for item in [parsed.meal_type, parsed.budget, parsed.occasion] if item
        )
        stage_start = time.monotonic()
        try:
            if location:
                search_context = self.search_provider.build_context(
                    parsed.area,
                    parsed.topic,
                    parsed.count,
                    context_hint=context_hint,
                    location=location,
                )
            else:
                search_context = self.search_provider.build_context(
                    parsed.area,
                    parsed.topic,
                    parsed.count,
                    context_hint=context_hint,
                )
        except Exception:
            search_elapsed = time.monotonic() - stage_start
            stage_ms["search_context"] = _ms(search_elapsed)
            self._record_event(
                self._event_for_request(
                    request_id,
                    chat_id,
                    RecommendationOutcome.SEARCH_CONTEXT_FAILED,
                    parsed,
                    parse_result=parse_result,
                    total_elapsed=time.monotonic() - total_start,
                    stage_ms=stage_ms,
                    failure_reason=RecommendationOutcome.SEARCH_CONTEXT_FAILED,
                    location_mode=bool(location),
                    dry_run=dry_run,
                )
            )
            self._log_stage(
                chat_id,
                "search_context",
                search_elapsed,
                failed=True,
            )
            self._log_stage(
                chat_id,
                "total",
                time.monotonic() - total_start,
                result="failed",
                failed_stage="search_context",
            )
            raise
        search_elapsed = time.monotonic() - stage_start
        stage_ms["search_context"] = _ms(search_elapsed)
        self._log_stage(
            chat_id,
            "search_context",
            search_elapsed,
            provider=search_context.used_provider or "",
            configured=search_context.configured,
            quota_blocked=search_context.quota_blocked,
            evidence_available=search_context.evidence_available,
            candidate_count=len(search_context.candidates),
            context_chars=len(search_context.text),
            kakao_candidate_count=search_context.stats.get("kakao_candidate_count"),
            naver_blog_evidence_count=search_context.stats.get("naver_blog_evidence_count"),
            matched_candidate_count=search_context.stats.get("matched_candidate_count"),
            location_mode=bool(location),
        )
        if not search_context.evidence_available:
            response = _search_evidence_unavailable_response(search_context)
            outcome = classify_evidence_unavailable(search_context.text)
            self._record_event(
                self._event_for_request(
                    request_id,
                    chat_id,
                    outcome,
                    parsed,
                    parse_result=parse_result,
                    search_context=search_context,
                    total_elapsed=time.monotonic() - total_start,
                    stage_ms=stage_ms,
                    failure_reason=outcome,
                    location_mode=bool(location),
                    dry_run=dry_run,
                )
            )
            self._log_stage(
                chat_id,
                "total",
                time.monotonic() - total_start,
                result="naver_evidence_unavailable",
                configured=search_context.configured,
                quota_blocked=search_context.quota_blocked,
                result_chars=len(response),
            )
            return response
        stage_start = time.monotonic()
        prompt = recommendation_prompt(
            parsed,
            datetime.now(),
            naver_context=search_context.text,
            request_text=request_text,
        )
        prompt_elapsed = time.monotonic() - stage_start
        stage_ms["prompt_build"] = _ms(prompt_elapsed)
        self._log_stage(
            chat_id,
            "prompt_build",
            prompt_elapsed,
            prompt_chars=len(prompt),
        )
        if dry_run:
            stage_start = time.monotonic()
            response = self._format_dry_run(parsed, search_context, prompt)
            format_elapsed = time.monotonic() - stage_start
            stage_ms["format"] = _ms(format_elapsed)
            self._record_event(
                self._event_for_request(
                    request_id,
                    chat_id,
                    RecommendationOutcome.DRY_RUN,
                    parsed,
                    parse_result=parse_result,
                    search_context=search_context,
                    total_elapsed=time.monotonic() - total_start,
                    stage_ms=stage_ms,
                    location_mode=bool(location),
                    dry_run=True,
                )
            )
            self._log_stage(
                chat_id,
                "format",
                format_elapsed,
                result_chars=len(response),
                dry_run=True,
            )
            self._log_stage(
                chat_id,
                "total",
                time.monotonic() - total_start,
                result="dry_run",
                result_chars=len(response),
            )
            return response

        stage_start = time.monotonic()
        try:
            raw = self.agent.generate(prompt)
        except Exception:
            agent_elapsed = time.monotonic() - stage_start
            stage_ms["agent_generate"] = _ms(agent_elapsed)
            self._record_event(
                self._event_for_request(
                    request_id,
                    chat_id,
                    RecommendationOutcome.AGENT_GENERATE_FAILED,
                    parsed,
                    parse_result=parse_result,
                    search_context=search_context,
                    total_elapsed=time.monotonic() - total_start,
                    stage_ms=stage_ms,
                    failure_reason=RecommendationOutcome.AGENT_GENERATE_FAILED,
                    location_mode=bool(location),
                    dry_run=dry_run,
                )
            )
            self._log_stage(
                chat_id,
                "agent_generate",
                agent_elapsed,
                failed=True,
            )
            self._log_stage(
                chat_id,
                "total",
                time.monotonic() - total_start,
                result="failed",
                failed_stage="agent_generate",
            )
            raise
        agent_elapsed = time.monotonic() - stage_start
        stage_ms["agent_generate"] = _ms(agent_elapsed)
        self._log_stage(
            chat_id,
            "agent_generate",
            agent_elapsed,
            raw_chars=len(raw),
        )
        stage_start = time.monotonic()
        result = parse_recommendation(raw, self.settings.blog_allowed_domains)
        response_parse_elapsed = time.monotonic() - stage_start
        stage_ms["response_parse"] = _ms(response_parse_elapsed)
        self._log_stage(
            chat_id,
            "response_parse",
            response_parse_elapsed,
            item_count=len(result.items),
            has_json=result.raw_json is not None,
        )
        if not result.items and result.raw_json is None and result.raw_text:
            response = "추천 결과 형식을 정리하지 못했어요. 잠시 후 다시 시도해주세요."
            self._record_event(
                self._event_for_request(
                    request_id,
                    chat_id,
                    RecommendationOutcome.INVALID_AGENT_RESPONSE,
                    parsed,
                    parse_result=parse_result,
                    search_context=search_context,
                    total_elapsed=time.monotonic() - total_start,
                    stage_ms=stage_ms,
                    failure_reason=RecommendationOutcome.INVALID_AGENT_RESPONSE,
                    location_mode=bool(location),
                    dry_run=dry_run,
                )
            )
            self._log_stage(
                chat_id,
                "total",
                time.monotonic() - total_start,
                result="invalid_agent_response",
                result_chars=len(response),
            )
            return response
        confirmed_blog_evidence = _confirmed_blog_evidence(
            search_context.verified_candidates,
            self.settings.blog_allowed_domains,
        )
        reconcile_stats = _reconcile_result_items(
            parsed,
            result,
            confirmed_blog_evidence,
            search_context.candidates,
            search_context.verified_candidates,
        )
        self._log_stage(
            chat_id,
            "evaluation_reconcile",
            0,
            initial_item_count=reconcile_stats.initial_item_count,
            item_count=reconcile_stats.item_count,
            candidate_count=reconcile_stats.candidate_count,
            evaluation_count=reconcile_stats.initial_item_count,
            accepted_evaluation_count=reconcile_stats.accepted_evaluation_count,
            rejected_evaluation_count=reconcile_stats.rejected_evaluation_count,
            filled_count=reconcile_stats.filled_count,
            exact_food_filtered_count=reconcile_stats.exact_food_filtered_count,
            weak_fit_filtered_count=reconcile_stats.weak_fit_filtered_count,
            confirmed_blog_url_count=reconcile_stats.confirmed_blog_url_count,
            confirmed_candidate_blog_link_count=reconcile_stats.confirmed_candidate_blog_link_count,
            drop_reasons=_format_drop_reasons(reconcile_stats.drop_reasons),
            diversity_group_count=_diversity_group_count(result.items),
            avg_confidence=_average_confidence(result.items),
            multi_blog_candidate_count=_multi_blog_candidate_count(result.items),
        )
        if reconcile_stats.changed:
            self._log_stage(
                chat_id,
                "result_filter",
                0,
                initial_item_count=reconcile_stats.initial_item_count,
                item_count=reconcile_stats.item_count,
                candidate_count=reconcile_stats.candidate_count,
                evaluation_count=reconcile_stats.initial_item_count,
                accepted_evaluation_count=reconcile_stats.accepted_evaluation_count,
                rejected_evaluation_count=reconcile_stats.rejected_evaluation_count,
                filled_count=reconcile_stats.filled_count,
                exact_food_filtered_count=reconcile_stats.exact_food_filtered_count,
                weak_fit_filtered_count=reconcile_stats.weak_fit_filtered_count,
                confirmed_blog_url_count=reconcile_stats.confirmed_blog_url_count,
                confirmed_candidate_blog_link_count=reconcile_stats.confirmed_candidate_blog_link_count,
                drop_reasons=_format_drop_reasons(reconcile_stats.drop_reasons),
                diversity_group_count=_diversity_group_count(result.items),
                avg_confidence=_average_confidence(result.items),
                multi_blog_candidate_count=_multi_blog_candidate_count(result.items),
            )
        partial_notice = ""
        if len(result.items) < parsed.count:
            if result.items:
                partial_notice = (
                    f"Kakao 장소와 네이버 블로그 근거가 함께 확인된 {len(result.items)}곳만 보여드려요. "
                    f"요청한 {parsed.count}곳 중 둘 다 확인되지 않은 후보는 제외했습니다.\n\n"
                )
                self._log_stage(
                    chat_id,
                    "confirmed_partial",
                    0,
                    item_count=len(result.items),
                    target_count=parsed.count,
                    confirmed_blog_url_count=len(confirmed_blog_evidence),
                )
            else:
                response = (
                    "Kakao 장소와 네이버 블로그 근거가 함께 확인된 후보를 찾지 못했어요. "
                    "다른 지역이나 더 넓은 요청으로 다시 시도해주세요."
                )
                self._record_event(
                    self._event_for_request(
                        request_id,
                        chat_id,
                        RecommendationOutcome.NO_CONFIRMED_BLOG_EVIDENCE,
                        parsed,
                        parse_result=parse_result,
                        search_context=search_context,
                        final_item_count=len(result.items),
                        total_elapsed=time.monotonic() - total_start,
                        stage_ms=stage_ms,
                        failure_reason=RecommendationOutcome.NO_CONFIRMED_BLOG_EVIDENCE,
                        location_mode=bool(location),
                        dry_run=dry_run,
                    )
                )
                self._log_stage(
                    chat_id,
                    "total",
                    time.monotonic() - total_start,
                    result="no_confirmed_blog_evidence",
                    item_count=len(result.items),
                    target_count=parsed.count,
                    confirmed_blog_url_count=len(confirmed_blog_evidence),
                    result_chars=len(response),
                )
                return response
        if not result.items:
            response = "이번 요청에서는 추천할 후보를 찾지 못했습니다."
            self._record_event(
                self._event_for_request(
                    request_id,
                    chat_id,
                    RecommendationOutcome.EMPTY_RESULT,
                    parsed,
                    parse_result=parse_result,
                    search_context=search_context,
                    final_item_count=len(result.items),
                    total_elapsed=time.monotonic() - total_start,
                    stage_ms=stage_ms,
                    failure_reason=RecommendationOutcome.EMPTY_RESULT,
                    location_mode=bool(location),
                    dry_run=dry_run,
                )
            )
            self._log_stage(
                chat_id,
                "total",
                time.monotonic() - total_start,
                result="empty_result",
                item_count=len(result.items),
                result_chars=len(response),
            )
            return response
        _attach_map_candidates(result.items, search_context.candidates)
        raw_to_store = raw if self.settings.store_raw_response else ""
        stage_start = time.monotonic()
        if self.store:
            self.store.add_result(
                chat_id=chat_id,
                request_text=request_text,
                area=parsed.area,
                topic=parsed.topic,
                search_keyword=result.search_keyword,
                raw_response=raw_to_store,
                items=result.items,
            )
        store_elapsed = time.monotonic() - stage_start
        stage_ms["store"] = _ms(store_elapsed)
        self._log_stage(
            chat_id,
            "store",
            store_elapsed,
            store_enabled=self.store is not None,
            raw_stored=bool(raw_to_store),
            item_count=len(result.items),
        )
        stage_start = time.monotonic()
        response = partial_notice + format_recommendation_message(
            result.search_keyword,
            result.items,
            area=parsed.area,
            decision_criteria=result.decision_criteria,
            top_summary=result.top_summary,
        )
        format_elapsed = time.monotonic() - stage_start
        stage_ms["format"] = _ms(format_elapsed)
        self._record_event(
            self._event_for_request(
                request_id,
                chat_id,
                RecommendationOutcome.OK,
                parsed,
                parse_result=parse_result,
                search_context=search_context,
                final_item_count=len(result.items),
                total_elapsed=time.monotonic() - total_start,
                stage_ms=stage_ms,
                partial=bool(partial_notice),
                location_mode=bool(location),
                dry_run=dry_run,
            )
        )
        self._log_stage(
            chat_id,
            "format",
            format_elapsed,
            result_chars=len(response),
            item_count=len(result.items),
        )
        self._log_stage(
            chat_id,
            "total",
            time.monotonic() - total_start,
            result="ok",
            result_chars=len(response),
            item_count=len(result.items),
            partial=bool(partial_notice),
        )
        return response

    def _format_dry_run(self, parsed: ParsedRequest, search_context: SearchContext, prompt: str) -> str:
        lines = [
            "dry-run: 실제 AI 에이전트 호출은 하지 않았습니다.",
            f"area={parsed.area}",
            f"topic={parsed.topic or '(empty)'}",
            f"count={parsed.count}",
            f"location_mode={bool(search_context.stats.get('location_mode', 0))}",
            f"search_provider={search_context.used_provider or '(none)'}",
            f"search_configured={search_context.configured}",
            f"quota_blocked={search_context.quota_blocked}",
            f"evidence_available={search_context.evidence_available}",
            f"candidate_count={len(search_context.candidates)}",
            f"kakao_candidate_count={search_context.stats.get('kakao_candidate_count', 0)}",
            f"naver_blog_evidence_count={search_context.stats.get('naver_blog_evidence_count', 0)}",
            f"matched_candidate_count={search_context.stats.get('matched_candidate_count', 0)}",
            f"context_chars={len(search_context.text)}",
            "",
            "prompt_preview:",
            prompt[:12000],
        ]
        return "\n".join(lines)

    def _log_stage(self, chat_id: str, stage: str, elapsed: float, **fields: object) -> None:
        if not self.logger.isEnabledFor(logging.INFO):
            return
        suffix = _format_log_fields(fields)
        if suffix:
            suffix = " " + suffix
        self.logger.info(
            "recommendation stage chat_id=%s stage=%s elapsed=%.2fs%s",
            _mask_identifier(chat_id),
            stage,
            elapsed,
            suffix,
        )

    def _event_for_request(
        self,
        request_id: str,
        chat_id: str,
        outcome: str,
        parsed: ParsedRequest,
        parse_result: RequestParseResult,
        search_context: SearchContext | None = None,
        final_item_count: int = 0,
        total_elapsed: float = 0,
        stage_ms: dict[str, int] | None = None,
        failure_reason: str = "",
        partial: bool = False,
        location_mode: bool = False,
        dry_run: bool = False,
    ) -> RecommendationEvent:
        stats = search_context.stats if search_context else {}
        return RecommendationEvent(
            request_id=request_id,
            chat_id=_mask_identifier(chat_id),
            outcome=outcome,
            failure_reason=failure_reason,
            parse_source=parse_result.source,
            parse_reason=parse_result.reason,
            parsed_intent=parsed.intent,
            parsed_area=parsed.area,
            parsed_topic=parsed.topic,
            parsed_count=parsed.count,
            target_count=parsed.count,
            kakao_candidate_count=_event_int(stats.get("kakao_candidate_count")),
            naver_blog_evidence_count=_event_int(stats.get("naver_blog_evidence_count")),
            matched_candidate_count=_event_int(stats.get("matched_candidate_count")),
            final_item_count=final_item_count,
            total_ms=_ms(total_elapsed),
            stage_ms=stage_ms or {},
            partial=partial,
            location_mode=location_mode,
            dry_run=dry_run,
        )

    def _record_event(self, event: RecommendationEvent) -> None:
        if self.event_recorder is None:
            return
        try:
            self.event_recorder.record(event)
        except Exception:
            self.logger.exception("recommendation event recording failed request_id=%s", event.request_id)


def _ms(elapsed: float) -> int:
    return max(0, int(round(elapsed * 1000)))


def _event_int(value: object) -> int:
    try:
        return int(value) if value is not None else 0
    except (TypeError, ValueError):
        return 0


def location_request_message() -> str:
    return (
        "현재 위치 기준으로 추천하려면 Telegram의 위치 공유 버튼으로 위치를 보내주세요.\n"
        "정확한 좌표는 이 요청을 처리하는 동안에만 사용합니다."
    )



def _search_evidence_unavailable_response(search_context: SearchContext) -> str:
    text = search_context.text
    if "Kakao Local API key is not configured" in text:
        return "Kakao Local 설정이 필요해요. KAKAO_REST_API_KEY를 설정한 뒤 다시 시도해주세요."
    if "Kakao Local search failed" in text:
        return "Kakao Local 장소 후보를 가져오지 못했어요. Kakao API 설정과 연결 상태를 확인한 뒤 다시 시도해주세요."
    if "Kakao Local search returned no usable restaurant candidates" in text:
        return "Kakao Local에서 사용할 수 있는 장소 후보를 찾지 못했어요. 지역이나 요청을 조금 넓혀 다시 시도해주세요."
    if "No Kakao Local candidates had matching Naver Blog evidence" in text:
        return "Kakao 장소와 네이버 블로그 근거가 함께 확인된 후보를 찾지 못했어요. 다른 지역이나 더 넓은 요청으로 다시 시도해주세요."
    if "Naver Blog search failed" in text:
        return "Naver Blog 근거를 가져오지 못했어요. Naver API 설정과 연결 상태를 확인한 뒤 다시 시도해주세요."
    if not search_context.configured:
        return "Kakao Local 또는 Naver Blog 설정이 필요해요. API 키 설정을 확인한 뒤 다시 시도해주세요."
    if search_context.quota_blocked:
        return "Naver Blog 근거를 가져오지 못했어요. 오늘 Naver API 한도 상태를 확인한 뒤 다시 시도해주세요."
    return "Kakao 장소와 Naver Blog 근거를 충분히 함께 확인하지 못했어요. 잠시 후 다시 시도해주세요."


_naver_evidence_unavailable_response = _search_evidence_unavailable_response


def _format_log_fields(fields: dict[str, object]) -> str:
    parts: list[str] = []
    for key, value in fields.items():
        if value is None:
            continue
        if isinstance(value, bool):
            text = "true" if value else "false"
        else:
            text = str(value)
        text = " ".join(text.split())
        parts.append(f"{key}={text}")
    return " ".join(parts)


def _mask_identifier(value: str) -> str:
    text = str(value)
    if len(text) <= 4:
        return "***"
    return "***" + text[-4:]
