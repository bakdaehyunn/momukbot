from __future__ import annotations

from momukbot.config import Settings
from momukbot.core.models import SearchCandidate, SearchContext
from momukbot.search.kakao import KakaoLocalCandidateProvider
from momukbot.search.naver import (
    BLOG_EVIDENCE_PER_CANDIDATE,
    SECONDARY_BLOG_DISPLAY,
    TARGETED_BLOG_DISPLAY,
    TARGETED_BLOG_SEARCH_LIMIT,
    BlogEvidence,
    LocalBlogMatch,
    NaverBlogEvidenceProvider,
    _candidate_key,
    _context_terms,
    _dedupe_blog_evidence,
    _format_verified_matches,
    _match_local_candidates_to_blog,
    _needs_second_wave,
    _secondary_blog_queries,
    _targeted_blog_query,
)
from momukbot.storage.quota import QuotaExceeded


class HybridSearchProvider:
    def __init__(
        self,
        settings: Settings,
        kakao_provider: KakaoLocalCandidateProvider | None = None,
        blog_provider: NaverBlogEvidenceProvider | None = None,
    ) -> None:
        self.settings = settings
        self.kakao = kakao_provider or KakaoLocalCandidateProvider(settings)
        self.blog = blog_provider or NaverBlogEvidenceProvider(settings)

    def build_context(
        self,
        area: str,
        topic: str,
        count: int = 30,
        context_hint: str = "",
    ) -> SearchContext:
        configured = self.kakao.configured and self.blog.configured
        if not self.kakao.configured:
            return SearchContext(
                text="Kakao Local API key is not configured. Set KAKAO_REST_API_KEY and try again.",
                configured=False,
                used_provider="kakao_local+naver_blog",
                evidence_available=False,
            )
        if not self.blog.configured:
            return SearchContext(
                text="Naver Blog API credentials are not configured. Set NAVER_CLIENT_ID/NAVER_CLIENT_SECRET and try again.",
                configured=False,
                used_provider="kakao_local+naver_blog",
                quota_blocked=True,
                evidence_available=False,
            )

        try:
            candidates = self.kakao.build_candidates(
                area=area,
                topic=topic,
                count=count,
                context_hint=context_hint,
            )
        except Exception as exc:
            return SearchContext(
                text=f"Kakao Local search failed: {exc}",
                used_provider="kakao_local+naver_blog",
                configured=configured,
                evidence_available=False,
            )

        if not candidates:
            return SearchContext(
                text="Kakao Local search returned no usable restaurant candidates.",
                used_provider="kakao_local+naver_blog",
                configured=configured,
                evidence_available=False,
                stats={"kakao_candidate_count": 0, "naver_blog_evidence_count": 0, "matched_candidate_count": 0},
            )

        try:
            evidence_items, matches = self._match_with_blog_evidence(
                area=area,
                topic=topic,
                count=count,
                context_hint=context_hint,
                candidates=candidates,
            )
        except QuotaExceeded:
            return SearchContext(
                text="Naver Blog API quota is blocked.",
                used_provider="kakao_local+naver_blog",
                configured=configured,
                quota_blocked=True,
                evidence_available=False,
                stats={
                    "kakao_candidate_count": len(candidates),
                    "naver_blog_evidence_count": 0,
                    "matched_candidate_count": 0,
                },
            )
        except Exception as exc:
            return SearchContext(
                text=f"Naver Blog search failed: {exc}",
                used_provider="kakao_local+naver_blog",
                configured=configured,
                evidence_available=False,
                stats={
                    "kakao_candidate_count": len(candidates),
                    "naver_blog_evidence_count": 0,
                    "matched_candidate_count": 0,
                },
            )

        if not matches:
            return SearchContext(
                text="No Kakao Local candidates had matching Naver Blog evidence.",
                used_provider="kakao_local+naver_blog",
                configured=configured,
                evidence_available=False,
                stats={
                    "kakao_candidate_count": len(candidates),
                    "naver_blog_evidence_count": len(evidence_items),
                    "matched_candidate_count": 0,
                },
            )

        return SearchContext(
            text="\n".join(_format_verified_matches(matches)).strip(),
            used_provider="kakao_local+naver_blog",
            configured=configured,
            evidence_available=True,
            candidates=[match.candidate for match in matches],
            stats={
                "kakao_candidate_count": len(candidates),
                "naver_blog_evidence_count": len(evidence_items),
                "matched_candidate_count": len(matches),
            },
        )

    def _match_with_blog_evidence(
        self,
        area: str,
        topic: str,
        count: int,
        context_hint: str,
        candidates: list[SearchCandidate],
    ) -> tuple[list[BlogEvidence], list[LocalBlogMatch]]:
        evidence_items: list[BlogEvidence] = []
        query_topic = "" if topic.strip() == "맛집" else topic
        query_base = " ".join(part for part in [area, query_topic] if part).strip()
        primary_query = f"{query_base} 맛집 후기".strip()
        searched_blog_queries = {primary_query}
        blog_display = min(100, max(30, count * 4))
        evidence_items.extend(
            self.blog.collect_blog_evidence(
                query=primary_query,
                area=area,
                topic=topic,
                display=blog_display,
                max_items=blog_display,
            )
        )

        context_terms = _context_terms(context_hint)
        if context_terms:
            context_query = " ".join([area, "맛집", *context_terms, "후기"]).strip()
            if context_query != primary_query:
                searched_blog_queries.add(context_query)
                evidence_items.extend(
                    self.blog.collect_blog_evidence(
                        query=context_query,
                        area=area,
                        topic=" ".join(part for part in [topic, *context_terms] if part),
                        display=min(30, max(10, count)),
                        max_items=min(30, max(10, count)),
                    )
                )

        evidence_items = _dedupe_blog_evidence(evidence_items)
        matches = _match_local_candidates_to_blog(candidates, evidence_items, count)
        if _needs_second_wave(matches, count):
            candidates = self.kakao.build_candidates(
                area=area,
                topic=topic,
                count=count,
                context_hint=context_hint,
                expanded=True,
                initial_candidates=candidates,
            )
            for query in _secondary_blog_queries(area, topic, context_hint):
                if query in searched_blog_queries:
                    continue
                searched_blog_queries.add(query)
                evidence_items.extend(
                    self.blog.collect_blog_evidence(
                        query=query,
                        area=area,
                        topic=topic,
                        display=SECONDARY_BLOG_DISPLAY,
                        max_items=SECONDARY_BLOG_DISPLAY,
                    )
                )
            evidence_items = _dedupe_blog_evidence(evidence_items)
            matches = _match_local_candidates_to_blog(candidates, evidence_items, count)

        if len(matches) < count:
            matched_keys = {_candidate_key(match.candidate) for match in matches}
            unmatched = [
                candidate
                for candidate in candidates
                if _candidate_key(candidate) not in matched_keys
            ]
            targeted_limit = min(
                TARGETED_BLOG_SEARCH_LIMIT,
                count - len(matches),
                len(unmatched),
            )
            for candidate in unmatched[:targeted_limit]:
                evidence_items.extend(
                    self.blog.collect_blog_evidence(
                        query=_targeted_blog_query(area, candidate),
                        area=area,
                        topic=topic,
                        display=TARGETED_BLOG_DISPLAY,
                        max_items=TARGETED_BLOG_DISPLAY,
                    )
                )
            evidence_items = _dedupe_blog_evidence(evidence_items)
            matches = _match_local_candidates_to_blog(candidates, evidence_items, count)

        return evidence_items, _limit_evidence_per_match(matches)


def _limit_evidence_per_match(matches: list[LocalBlogMatch]) -> list[LocalBlogMatch]:
    limited: list[LocalBlogMatch] = []
    for match in matches:
        limited.append(
            LocalBlogMatch(
                candidate=match.candidate,
                evidence=match.evidence[:BLOG_EVIDENCE_PER_CANDIDATE],
                candidate_index=match.candidate_index,
            )
        )
    return limited
