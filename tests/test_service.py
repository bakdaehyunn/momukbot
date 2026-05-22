import logging
from pathlib import Path

from momukbot.config import Settings
from momukbot.core.models import SearchCandidate, SearchContext
from momukbot.core.service import RecommendationService, parse_recommendation


class FakeAgent:
    def generate(self, prompt: str) -> str:
        return recommendation_json(30)


class UnderfilledAgent:
    def __init__(self) -> None:
        self.prompts: list[str] = []

    def generate(self, prompt: str) -> str:
        self.prompts.append(prompt)
        return recommendation_json(13)


class CafeMixedAgent:
    def __init__(self) -> None:
        self.prompts: list[str] = []

    def generate(self, prompt: str) -> str:
        self.prompts.append(prompt)
        return recommendation_json(30, first_name="스타벅스 목동역점", first_category="카페")


class MismatchedBlogAgent:
    def __init__(self) -> None:
        self.prompts: list[str] = []

    def generate(self, prompt: str) -> str:
        self.prompts.append(prompt)
        return recommendation_json(30, first_name="전혀다른가게", first_category="국밥")


class ShortNameFalsePositiveAgent:
    def generate(self, prompt: str) -> str:
        return """
        {
          "search_keyword": "목동역 맛집",
          "items": [
            {
              "name": "하이",
              "category": "술집",
              "status_marker": "영업시간 미확인",
              "reason": "짧은 이름 후보입니다.",
              "links": [{"label": "네이버 블로그", "url": "https://blog.naver.com/v/hi"}]
            }
          ]
        }
        """


class EmptyItemsAgent:
    def generate(self, prompt: str) -> str:
        return """
        {
          "search_keyword": "목동역 무한리필",
          "items": []
        }
        """


class RawTextAgent:
    def generate(self, prompt: str) -> str:
        return "not valid recommendation json"


class RerankingAgent:
    def __init__(self) -> None:
        self.prompts: list[str] = []

    def generate(self, prompt: str) -> str:
        self.prompts.append(prompt)
        return """
        {
          "search_keyword": "서면 혼밥",
          "decision_criteria": ["혼밥", "조용한 분위기", "블로그 근거"],
          "top_summary": "혼밥과 조용한 분위기를 우선해 검증 후보를 재정렬했습니다.",
          "items": [
            {
              "name": "조용한밥집",
              "category": "한식",
              "status_marker": "영업시간 미확인",
              "fit_tags": ["혼밥", "조용함"],
              "tradeoff": "메뉴 폭은 넓지 않을 수 있습니다.",
              "reason": "혼밥과 조용한 분위기 언급이 있어 요청에 가장 잘 맞습니다.",
              "links": [{"label": "네이버 블로그", "url": "https://blog.naver.com/v/2"}]
            },
            {
              "name": "시끄러운고기집",
              "category": "한식",
              "status_marker": "영업시간 미확인",
              "fit_tags": ["고기", "회식"],
              "tradeoff": "혼밥 요청에는 상대적으로 덜 맞습니다.",
              "reason": "고기 메뉴 후기가 확인되지만 회식 분위기라 우선순위는 낮습니다.",
              "links": [{"label": "네이버 블로그", "url": "https://blog.naver.com/v/1"}]
            },
            {
              "name": "없는가게",
              "category": "한식",
              "status_marker": "영업시간 미확인",
              "fit_tags": ["검증안됨"],
              "tradeoff": "검증되지 않은 후보입니다.",
              "reason": "검증되지 않은 후보입니다.",
              "links": [{"label": "네이버 블로그", "url": "https://blog.naver.com/v/1"}]
            }
          ]
        }
        """


class FitScoredAgent:
    def __init__(self) -> None:
        self.prompts: list[str] = []

    def generate(self, prompt: str) -> str:
        self.prompts.append(prompt)
        return """
        {
          "search_keyword": "서면 혼밥",
          "decision_criteria": ["혼밥", "조용한 분위기"],
          "top_summary": "혼자 먹기 편한 곳을 앞쪽에 두었습니다.",
          "items": [
            {
              "name": "시끄러운고기집",
              "category": "한식",
              "status_marker": "영업시간 미확인",
              "intent_fit": 2,
              "meal_fit": 5,
              "occasion_fit": 1,
              "risk_flags": ["occasion_mismatch"],
              "fit_tags": ["고기", "회식"],
              "tradeoff": "혼밥 요청에는 상대적으로 덜 맞습니다.",
              "reason": "고기 메뉴 후기가 확인되지만 회식 분위기라 우선순위는 낮습니다.",
              "links": [{"label": "네이버 블로그", "url": "https://blog.naver.com/v/1"}]
            },
            {
              "name": "조용한밥집",
              "category": "한식",
              "status_marker": "영업시간 미확인",
              "intent_fit": 5,
              "meal_fit": 4,
              "occasion_fit": 5,
              "risk_flags": [],
              "fit_tags": ["혼밥", "조용함"],
              "tradeoff": "",
              "reason": "혼밥과 조용한 분위기 언급이 있어 요청에 가장 잘 맞습니다.",
              "links": [{"label": "네이버 블로그", "url": "https://blog.naver.com/v/2"}]
            },
            {
              "name": "든든국밥",
              "category": "국밥",
              "status_marker": "영업시간 미확인",
              "intent_fit": 4,
              "meal_fit": 5,
              "occasion_fit": 4,
              "risk_flags": [],
              "fit_tags": ["혼밥", "국밥"],
              "tradeoff": "",
              "reason": "혼밥 손님도 편하게 먹었다는 후기가 있어 요청에 맞습니다.",
              "links": [{"label": "네이버 블로그", "url": "https://blog.naver.com/v/3"}]
            }
          ]
        }
        """


class EvaluationAgent:
    def __init__(self) -> None:
        self.prompts: list[str] = []

    def generate(self, prompt: str) -> str:
        self.prompts.append(prompt)
        return """
        {
          "search_keyword": "서면 혼밥",
          "decision_criteria": ["혼밥하기 편한 단품 메뉴", "혼자 들어가기 부담 적은 곳"],
          "top_summary": "조용한밥집은 혼자 먹기 편하고, 든든국밥은 빠르게 든든한 한 끼가 필요할 때 좋습니다.",
          "evaluations": [
            {
              "name": "시끄러운고기집",
              "intent_fit": 2,
              "meal_fit": 5,
              "occasion_fit": 1,
              "evidence_quality": 4,
              "risk_flags": ["occasion_mismatch"],
              "fit_tags": ["고기", "회식"],
              "tradeoff": "혼밥 요청에는 상대적으로 덜 맞습니다.",
              "reason": "고기 메뉴는 좋지만 회식 분위기라 혼밥 우선순위는 낮습니다."
            },
            {
              "name": "조용한밥집",
              "intent_fit": 5,
              "meal_fit": 4,
              "occasion_fit": 5,
              "evidence_quality": 4,
              "risk_flags": [],
              "fit_tags": ["혼밥", "조용함"],
              "tradeoff": "",
              "reason": "혼자 먹기 편하고 조용한 분위기라 요청에 가장 잘 맞습니다."
            },
            {
              "name": "든든국밥",
              "intent_fit": 4,
              "meal_fit": 5,
              "occasion_fit": 4,
              "evidence_quality": 4,
              "risk_flags": [],
              "fit_tags": ["혼밥", "국밥"],
              "tradeoff": "",
              "reason": "혼자 빠르게 든든한 국밥을 먹기 좋은 후보입니다."
            },
            {
              "name": "없는가게",
              "intent_fit": 5,
              "meal_fit": 5,
              "occasion_fit": 5,
              "evidence_quality": 5,
              "risk_flags": [],
              "fit_tags": ["검증안됨"],
              "tradeoff": "",
              "reason": "후보에 없는 가게입니다."
            }
          ]
        }
        """


class UnderfilledVerifiedAgent:
    def generate(self, prompt: str) -> str:
        return """
        {
          "search_keyword": "서면 혼밥",
          "decision_criteria": ["혼밥"],
          "top_summary": "혼밥 기준으로 먼저 볼 후보를 골랐습니다.",
          "items": [
            {
              "name": "조용한밥집",
              "category": "한식",
              "status_marker": "영업시간 미확인",
              "fit_tags": ["혼밥"],
              "tradeoff": "영업시간은 확인되지 않았습니다.",
              "reason": "혼밥하기 좋다는 후기가 있어 요청에 맞습니다.",
              "links": [{"label": "네이버 블로그", "url": "https://blog.naver.com/v/2"}]
            }
          ]
        }
        """


class FakeSearch:
    def __init__(self) -> None:
        self.context_hint = ""

    def build_context(
        self,
        area: str,
        topic: str,
        count: int = 30,
        context_hint: str = "",
    ) -> SearchContext:
        self.context_hint = context_hint
        evidence_lines = []
        for index in range(1, count + 1):
            name = "송정3대국밥" if index == 1 else f"서면국밥{index}"
            evidence_lines.append(
                f"{index}. title={name} 방문 후기 url=https://blog.naver.com/a/{index} summary={name} 블로그 후기"
            )
        return SearchContext(
            text=f"Naver Blog evidence context_hint={context_hint}\n" + "\n".join(evidence_lines),
            used_provider="fake",
            configured=True,
        )


class LocalMapSearch(FakeSearch):
    def build_context(
        self,
        area: str,
        topic: str,
        count: int = 30,
        context_hint: str = "",
    ) -> SearchContext:
        base = super().build_context(area, topic, count=count, context_hint=context_hint)
        return SearchContext(
            text=base.text,
            used_provider=base.used_provider,
            configured=base.configured,
            candidates=[
                SearchCandidate(
                    name="송정3대국밥",
                    category="국밥",
                    address="부산 부산진구 서면로 68",
                    url="https://naver.me/FAjSYD1g",
                    source="naver_local",
                )
            ],
        )


class NonNaverLocalLinkSearch(FakeSearch):
    def build_context(
        self,
        area: str,
        topic: str,
        count: int = 30,
        context_hint: str = "",
    ) -> SearchContext:
        base = super().build_context(area, topic, count=count, context_hint=context_hint)
        return SearchContext(
            text=base.text,
            used_provider=base.used_provider,
            configured=base.configured,
            candidates=[
                SearchCandidate(
                    name="송정3대국밥",
                    category="국밥",
                    address="서울 양천구 목동로 221",
                    url="https://instagram.com/place",
                    source="naver_local",
                )
            ],
        )


class VerifiedCandidateSearch:
    candidates = [
        SearchCandidate(
            name="시끄러운고기집",
            category="한식",
            address="부산 부산진구 서면로 1",
            source="naver_local",
        ),
        SearchCandidate(
            name="조용한밥집",
            category="한식",
            address="부산 부산진구 서면로 2",
            source="naver_local",
        ),
        SearchCandidate(
            name="든든국밥",
            category="국밥",
            address="부산 부산진구 서면로 3",
            source="naver_local",
        ),
    ]

    def build_context(
        self,
        area: str,
        topic: str,
        count: int = 30,
        context_hint: str = "",
    ) -> SearchContext:
        return SearchContext(
            text="\n".join(
                [
                    "Verified Naver Local + Naver Blog evidence matches.",
                    "1. place=시끄러운고기집 category=한식 address=부산 부산진구 서면로 1 best_blog_score=10",
                    "1.1 place=시끄러운고기집 blog_url=https://blog.naver.com/v/1 blog_title=서면 고기 맛집 시끄러운고기집 blog_summary=회식 방문 후기가 많고 고기 메뉴가 좋았습니다.",
                    "2. place=조용한밥집 category=한식 address=부산 부산진구 서면로 2 best_blog_score=9",
                    "2.1 place=조용한밥집 blog_url=https://blog.naver.com/v/2 blog_title=서면 혼밥 맛집 조용한밥집 blog_summary=혼밥하기 좋고 조용한 분위기라는 방문 후기입니다.",
                    "3. place=든든국밥 category=국밥 address=부산 부산진구 서면로 3 best_blog_score=8",
                    "3.1 place=든든국밥 blog_url=https://blog.naver.com/v/3 blog_title=서면 국밥 맛집 든든국밥 blog_summary=혼밥 손님도 편하게 먹었다는 방문 후기입니다.",
                ]
            ),
            used_provider="fake",
            configured=True,
            candidates=self.candidates,
        )


class NoEvidenceSearch(FakeSearch):
    def build_context(
        self,
        area: str,
        topic: str,
        count: int = 30,
        context_hint: str = "",
    ) -> SearchContext:
        self.context_hint = context_hint
        return SearchContext(
            text="Naver API quota is blocked.",
            used_provider="fake",
            configured=True,
            quota_blocked=True,
            evidence_available=False,
        )


class BloglessSearch(FakeSearch):
    def build_context(
        self,
        area: str,
        topic: str,
        count: int = 30,
        context_hint: str = "",
    ) -> SearchContext:
        self.context_hint = context_hint
        return SearchContext(
            text="Naver Blog search returned no usable evidence.",
            used_provider="fake",
            configured=True,
            evidence_available=False,
        )


class ShortNameFalsePositiveSearch(FakeSearch):
    def build_context(
        self,
        area: str,
        topic: str,
        count: int = 30,
        context_hint: str = "",
    ) -> SearchContext:
        return SearchContext(
            text="\n".join(
                [
                    "Verified Naver Local + Naver Blog evidence matches.",
                    "1. place=하이 category=술집 address=서울 양천구 목동 best_blog_score=10",
                    "1.1 place=하이 blog_url=https://blog.naver.com/v/hi blog_title=목동역 맛집 오목교곱창 후기 blog_summary=곱창과 하이볼까지 맛있게 먹고 왔습니다.",
                ]
            ),
            used_provider="fake",
            configured=True,
            evidence_available=True,
            candidates=[
                SearchCandidate(
                    name="하이",
                    category="술집",
                    address="서울 양천구 목동",
                    source="naver_local",
                )
            ],
        )


class UnlimitedRefillSearch(FakeSearch):
    def build_context(
        self,
        area: str,
        topic: str,
        count: int = 30,
        context_hint: str = "",
    ) -> SearchContext:
        return SearchContext(
            text="\n".join(
                [
                    "Verified Naver Local + Naver Blog evidence matches.",
                    "1. place=편편집 목동사거리점 category=무한리필 address=서울 강서구 곰달래로 267 best_blog_score=15",
                    "1.1 place=편편집 목동사거리점 blog_url=https://blog.naver.com/v/unlimited blog_title=목동역 무한리필 샤브샤브 편편집 목동사거리점 방문 후기 blog_summary=월남쌈과 샐러드바를 무제한으로 먹을 수 있고 여럿이 가기 좋았습니다.",
                ]
            ),
            used_provider="fake",
            configured=True,
            evidence_available=True,
            candidates=[
                SearchCandidate(
                    name="편편집 목동사거리점",
                    category="무한리필",
                    address="서울 강서구 곰달래로 267",
                    source="naver_local",
                )
            ],
        )


class RecordingStore:
    def __init__(self) -> None:
        self.raw_response = ""
        self.item_count = 0
        self.add_count = 0

    def add_result(
        self,
        chat_id: str,
        request_text: str,
        area: str,
        topic: str,
        search_keyword: str,
        raw_response: str,
        items,
    ) -> None:
        self.raw_response = raw_response
        self.item_count = len(items)
        self.add_count += 1


def recommendation_json(
    count: int,
    first_name: str = "송정3대국밥",
    first_category: str = "국밥",
) -> str:
    items: list[str] = []
    for index in range(1, count + 1):
        name = first_name if index == 1 else f"서면국밥{index}"
        category = first_category if index == 1 else "국밥"
        items.append(
            f"""{{
              "name": "{name}",
              "category": "{category}",
              "status_marker": "영업 가능성 높음",
              "reason": "블로그 후기가 많습니다.",
              "links": [{{"label": "리뷰", "url": "https://blog.naver.com/a/{index}"}}]
            }}"""
        )
    return f"""
    {{
      "search_keyword": "서면 해장",
      "items": [{",".join(items)}]
    }}
    """


def settings(tmp_path: Path, store_raw_response: bool = False) -> Settings:
    return Settings(
        telegram_bot_token="",
        telegram_allowed_chat_ids=(),
        telegram_admin_user_ids=(),
        naver_client_id="",
        naver_client_secret="",
        naver_daily_soft_limit=10,
        blog_allowed_domains=("blog.naver.com",),
        agent_provider="codex_cli",
        codex_bin="codex",
        codex_workdir=tmp_path,
        codex_sandbox="read-only",
        codex_timeout_sec=60,
        default_count=30,
        state_dir=tmp_path,
        log_dir=tmp_path,
        store_raw_response=store_raw_response,
    )


def test_parse_recommendation_json() -> None:
    result = parse_recommendation(FakeAgent().generate("x"))

    assert result.search_keyword == "서면 해장"
    assert result.items[0].name == "송정3대국밥"
    assert result.items[0].links[0]["label"] == "네이버 블로그"


def test_parse_recommendation_keeps_reasoning_fields() -> None:
    result = parse_recommendation(
        """
        {
          "search_keyword": "서면 혼밥",
          "decision_criteria": ["혼밥", "조용함"],
          "top_summary": "혼밥 가능성과 조용한 분위기를 우선했습니다.",
          "items": [
            {
              "name": "조용한밥집",
              "category": "한식",
              "status_marker": "영업시간 미확인",
              "intent_fit": 5,
              "meal_fit": 4,
              "occasion_fit": 5,
              "risk_flags": ["영업시간 미확인"],
              "fit_tags": ["혼밥", "조용함"],
              "tradeoff": "영업시간은 확인되지 않았습니다.",
              "reason": "혼밥 후기가 있어 요청에 잘 맞습니다.",
              "links": [{"label": "네이버 블로그", "url": "https://blog.naver.com/v/2"}]
            }
          ]
        }
        """
    )

    assert result.decision_criteria == ["혼밥", "조용함"]
    assert result.top_summary == "혼밥 가능성과 조용한 분위기를 우선했습니다."
    assert result.items[0].intent_fit == 5
    assert result.items[0].meal_fit == 4
    assert result.items[0].occasion_fit == 5
    assert result.items[0].risk_flags == ["영업시간 미확인"]
    assert result.items[0].fit_tags == ["혼밥", "조용함"]
    assert result.items[0].tradeoff == "영업시간은 확인되지 않았습니다."


def test_parse_recommendation_accepts_candidate_evaluations_without_links() -> None:
    result = parse_recommendation(
        """
        {
          "search_keyword": "서면 혼밥",
          "decision_criteria": ["혼밥하기 편한 단품 메뉴"],
          "top_summary": "혼자 먹기 편한 후보를 우선했습니다.",
          "evaluations": [
            {
              "name": "조용한밥집",
              "intent_fit": 5,
              "meal_fit": 4,
              "occasion_fit": 5,
              "evidence_quality": 4,
              "risk_flags": [],
              "fit_tags": ["혼밥", "조용함"],
              "tradeoff": "",
              "reason": "혼자 먹기 편하고 조용한 분위기라 요청에 맞습니다."
            }
          ]
        }
        """
    )

    assert result.search_keyword == "서면 혼밥"
    assert result.items[0].name == "조용한밥집"
    assert result.items[0].intent_fit == 5
    assert result.items[0].meal_fit == 4
    assert result.items[0].occasion_fit == 5
    assert result.items[0].fit_tags == ["혼밥", "조용함"]
    assert result.items[0].links == []


def test_service_dry_run_does_not_call_agent(tmp_path: Path) -> None:
    service = RecommendationService(settings(tmp_path), FakeAgent(), FakeSearch())
    response = service.handle_text("cli", "서면에서 해장 국밥 추천해줘", dry_run=True)

    assert response is not None
    assert "dry-run" in response
    assert "실제 AI 에이전트 호출은 하지 않았습니다" in response
    assert "Need: evaluate up to 30 verified candidates" in response
    assert "Use open-status markers only when supported by the provided context" in response
    assert "Return up to 30 evaluations" in response
    assert "Do not return fewer items because operating hours are uncertain" in response
    assert "stay within the same food intent" in response
    assert "Do not use Tistory" in response
    assert "do not perform any additional web searches" in response
    assert "Do not include URLs or link objects" in response
    assert "Candidate names not present in the provided verified candidate context are rejected" in response
    assert "Original user request: 서면에서 해장 국밥 추천해줘" in response
    assert "You may reorder verified candidates to fit the original user request" in response
    assert "Your main job is candidate evaluation, request-aware ranking" in response
    assert "Evaluate every candidate against the original user request" in response
    assert "Write `top_summary` as a practical guide to the first three returned places" in response
    assert "Do not write source-checking statements as the main user-facing reason" in response
    assert '"intent_fit": 0' in response
    assert '"meal_fit": 0' in response
    assert '"occasion_fit": 0' in response
    assert '"evidence_quality": 0' in response
    assert '"risk_flags": []' in response
    assert "deterministic local candidate roster" not in response
    assert "Do not search again just to verify operating hours" in response
    assert 'General "맛집" means meal-serving restaurants' in response
    assert "Exclude cafes, coffee chains, dessert-only shops" in response
    assert '"category": "국밥|감자탕|해장국|술집|일식|중식|한식|무한리필|샤브샤브|기타"' in response
    assert "tistory.com" not in response
    assert "Naver Blog" in response


def test_service_dry_run_allows_cafe_for_explicit_coffee_request(tmp_path: Path) -> None:
    service = RecommendationService(settings(tmp_path), FakeAgent(), FakeSearch())
    response = service.handle_text("cli", "목동역 커피 추천", dry_run=True)

    assert response is not None
    assert "area=목동역" in response
    assert "topic=커피" in response
    assert "The user explicitly asked for cafe/coffee/dessert/bakery" in response
    assert '"category": "국밥|감자탕|해장국|술집|카페|일식|중식|한식|무한리필|샤브샤브|기타"' in response


def test_service_formats_agent_response(tmp_path: Path) -> None:
    service = RecommendationService(settings(tmp_path), FakeAgent(), FakeSearch())
    response = service.handle_text("cli", "서면에서 해장 국밥 추천해줘")

    assert response is not None
    assert "송정3대국밥" in response
    assert "지도:" in response


def test_service_uses_llm_as_reranking_step_with_code_validation(tmp_path: Path) -> None:
    agent = RerankingAgent()
    store = RecordingStore()
    service = RecommendationService(settings(tmp_path), agent, VerifiedCandidateSearch(), store)  # type: ignore[arg-type]

    response = service.handle_text("cli", "서면 혼밥 맛집 3곳 추천")

    assert response is not None
    assert "Original user request: 서면 혼밥 맛집 3곳 추천" in agent.prompts[0]
    assert "You may reorder verified candidates to fit the original user request" in agent.prompts[0]
    assert response.find("1. 조용한밥집") < response.find("2. 시끄러운고기집")
    assert "이번 요청 기준: 혼밥, 조용한 분위기" in response
    assert "블로그 근거" not in response
    assert "검증 후보" not in response
    assert "포인트: 혼밥 · 조용함" in response
    assert "참고: 메뉴 폭은 넓지 않을 수 있습니다." in response
    assert "없는가게" not in response
    assert "든든국밥" in response
    assert (
        "먼저 볼 3곳:\n"
        "- 조용한밥집: 혼밥 · 조용함\n"
        "- 시끄러운고기집: 고기 · 회식\n"
        "- 든든국밥: 혼밥"
        in response
    )
    assert store.item_count == 3


def test_service_prompt_guides_unlimited_refill_ranking(tmp_path: Path) -> None:
    agent = UnderfilledAgent()
    service = RecommendationService(settings(tmp_path), agent, FakeSearch())

    service.handle_text("cli", "목동역 무한리필 샤브샤브 추천")

    assert "무한리필" in agent.prompts[0]
    assert "unlimited_refill" in agent.prompts[0]
    assert "혼밥 요청에는 무한리필" in agent.prompts[0]


def test_service_orders_verified_candidates_by_llm_fit_scores(tmp_path: Path) -> None:
    agent = FitScoredAgent()
    store = RecordingStore()
    service = RecommendationService(settings(tmp_path), agent, VerifiedCandidateSearch(), store)  # type: ignore[arg-type]

    response = service.handle_text("cli", "서면 혼밥 맛집 3곳 추천")

    assert response is not None
    assert "Evaluate every candidate against the original user request" in agent.prompts[0]
    assert response.find("1. 조용한밥집") < response.find("2. 든든국밥")
    assert response.find("2. 든든국밥") < response.find("3. 시끄러운고기집")
    assert (
        "먼저 볼 3곳:\n"
        "- 조용한밥집: 혼밥 · 조용함\n"
        "- 든든국밥: 혼밥 · 국밥\n"
        "- 시끄러운고기집: 고기 · 회식"
        in response
    )
    assert store.item_count == 3


def test_service_uses_llm_candidate_evaluations_with_code_owned_links(tmp_path: Path) -> None:
    agent = EvaluationAgent()
    store = RecordingStore()
    service = RecommendationService(settings(tmp_path), agent, VerifiedCandidateSearch(), store)  # type: ignore[arg-type]

    response = service.handle_text("cli", "서면 혼밥 맛집 3곳 추천")

    assert response is not None
    assert "Return ONLY candidate evaluation JSON" in agent.prompts[0]
    assert '"evaluations"' in agent.prompts[0]
    assert response.find("1. 조용한밥집") < response.find("2. 든든국밥")
    assert response.find("2. 든든국밥") < response.find("3. 시끄러운고기집")
    assert "없는가게" not in response
    assert "블로그: https://blog.naver.com/v/2" in response
    assert "블로그: https://blog.naver.com/v/3" in response
    assert store.item_count == 3


def test_service_logs_llm_evaluation_reconcile_stats(tmp_path: Path, caplog) -> None:
    logger = logging.getLogger("momukbot.test.evaluation_stats")
    service = RecommendationService(settings(tmp_path), EvaluationAgent(), VerifiedCandidateSearch(), logger=logger)

    with caplog.at_level(logging.INFO, logger=logger.name):
        response = service.handle_text("123456789", "서면 혼밥 맛집 3곳 추천")

    assert response is not None
    result_filter_messages = [
        record.getMessage()
        for record in caplog.records
        if "stage=result_filter" in record.getMessage()
    ]
    assert result_filter_messages
    message = result_filter_messages[-1]
    assert "candidate_count=3" in message
    assert "evaluation_count=4" in message
    assert "accepted_evaluation_count=3" in message
    assert "rejected_evaluation_count=1" in message
    assert "filled_count=0" in message
    assert "confirmed_blog_url_count=3" in message
    assert "confirmed_candidate_blog_link_count=3" in message


def test_service_always_logs_evaluation_reconcile_stats_when_unchanged(tmp_path: Path, caplog) -> None:
    logger = logging.getLogger("momukbot.test.evaluation_reconcile")
    service = RecommendationService(settings(tmp_path), FitScoredAgent(), VerifiedCandidateSearch(), logger=logger)

    with caplog.at_level(logging.INFO, logger=logger.name):
        response = service.handle_text("123456789", "서면 혼밥 맛집 3곳 추천")

    assert response is not None
    messages = [record.getMessage() for record in caplog.records]
    reconcile_messages = [message for message in messages if "stage=evaluation_reconcile" in message]
    assert reconcile_messages
    message = reconcile_messages[-1]
    assert "candidate_count=3" in message
    assert "evaluation_count=3" in message
    assert "accepted_evaluation_count=3" in message
    assert "rejected_evaluation_count=0" in message
    assert "filled_count=0" in message
    assert "confirmed_blog_url_count=3" in message
    assert "confirmed_candidate_blog_link_count=3" in message


def test_service_fills_missing_verified_candidates_after_llm_step(tmp_path: Path) -> None:
    store = RecordingStore()
    service = RecommendationService(
        settings(tmp_path),
        UnderfilledVerifiedAgent(),
        VerifiedCandidateSearch(),
        store,  # type: ignore[arg-type]
    )

    response = service.handle_text("cli", "서면 혼밥 맛집 3곳 추천")

    assert response is not None
    assert not response.startswith("네이버 블로그 근거가 확인된")
    assert "서면 혼밥 추천 3곳" in response
    assert "1. 조용한밥집" in response
    assert "시끄러운고기집" in response
    assert "든든국밥" in response
    assert store.item_count == 3


def test_service_attaches_naver_local_map_details(tmp_path: Path) -> None:
    service = RecommendationService(settings(tmp_path), FakeAgent(), LocalMapSearch())
    response = service.handle_text("cli", "서면에서 해장 국밥 추천해줘")

    assert response is not None
    assert (
        "   주소: 부산 부산진구 서면로 68\n"
        "   지도: https://naver.me/FAjSYD1g"
        in response
    )
    assert "app.map.naver.com/launchApp" not in response


def test_service_does_not_use_non_naver_local_link_as_map_url(tmp_path: Path) -> None:
    service = RecommendationService(settings(tmp_path), FakeAgent(), NonNaverLocalLinkSearch())
    response = service.handle_text("cli", "목동역 맛집 추천")

    assert response is not None
    assert "서울 양천구 목동로 221" in response
    assert "instagram.com" not in response
    assert "https://map.naver.com/p/search/" in response
    assert "app.map.naver.com/launchApp" not in response
    assert "nmap://search?query=" not in response


def test_service_does_not_call_agent_when_naver_evidence_is_unavailable(tmp_path: Path) -> None:
    agent = UnderfilledAgent()
    store = RecordingStore()
    service = RecommendationService(settings(tmp_path), agent, NoEvidenceSearch(), store)  # type: ignore[arg-type]

    response = service.handle_text("cli", "서면에서 해장 국밥 추천해줘")

    assert response == "Naver 근거를 가져오지 못했어요. 오늘 Naver API 한도 상태를 확인한 뒤 다시 시도해주세요."
    assert agent.prompts == []
    assert store.add_count == 0


def test_service_sends_confirmed_partial_result_without_completion_retry(tmp_path: Path) -> None:
    agent = UnderfilledAgent()
    store = RecordingStore()
    service = RecommendationService(settings(tmp_path), agent, FakeSearch(), store)  # type: ignore[arg-type]

    response = service.handle_text("cli", "서면에서 해장 국밥 추천해줘")

    assert response is not None
    assert response.startswith("네이버 블로그 근거가 확인된 13곳만 보여드려요.")
    assert "서면 해장 추천 13곳" in response
    assert len(agent.prompts) == 1
    assert store.item_count == 13
    assert store.add_count == 1


def test_service_rejects_local_candidate_fallback_without_blog_evidence(tmp_path: Path) -> None:
    agent = UnderfilledAgent()
    store = RecordingStore()
    service = RecommendationService(settings(tmp_path), agent, BloglessSearch(), store)  # type: ignore[arg-type]

    response = service.handle_text("cli", "목동역 맛집 추천")

    assert response == "Naver 근거를 충분히 가져오지 못했어요. 잠시 후 다시 시도해주세요."
    assert agent.prompts == []
    assert store.add_count == 0


def test_service_filters_cafe_results_for_general_restaurant_request(tmp_path: Path) -> None:
    agent = CafeMixedAgent()
    store = RecordingStore()
    service = RecommendationService(settings(tmp_path), agent, FakeSearch(), store)  # type: ignore[arg-type]

    response = service.handle_text("cli", "목동역 맛집 추천")

    assert response is not None
    assert "스타벅스" not in response
    assert response.startswith("네이버 블로그 근거가 확인된 29곳만 보여드려요.")
    assert len(agent.prompts) == 1
    assert store.item_count == 29
    assert store.add_count == 1


def test_service_rejects_blog_link_for_different_place(tmp_path: Path) -> None:
    agent = MismatchedBlogAgent()
    store = RecordingStore()
    service = RecommendationService(settings(tmp_path), agent, FakeSearch(), store)  # type: ignore[arg-type]

    response = service.handle_text("cli", "서면에서 해장 국밥 추천해줘")

    assert response is not None
    assert response.startswith("네이버 블로그 근거가 확인된 29곳만 보여드려요.")
    assert len(agent.prompts) == 1
    assert store.item_count == 29
    assert store.add_count == 1


def test_service_rejects_short_name_substring_blog_false_positive(tmp_path: Path) -> None:
    store = RecordingStore()
    service = RecommendationService(
        settings(tmp_path),
        ShortNameFalsePositiveAgent(),
        ShortNameFalsePositiveSearch(),
        store,  # type: ignore[arg-type]
    )

    response = service.handle_text("cli", "목동역 맛집 1곳 추천")

    assert response == "네이버 블로그 근거가 확인된 후보를 찾지 못했어요. 다른 지역이나 더 넓은 요청으로 다시 시도해주세요."
    assert store.add_count == 0


def test_service_fallback_marks_unlimited_refill_tradeoff(tmp_path: Path) -> None:
    store = RecordingStore()
    service = RecommendationService(
        settings(tmp_path),
        EmptyItemsAgent(),
        UnlimitedRefillSearch(),
        store,  # type: ignore[arg-type]
    )

    response = service.handle_text("cli", "목동역 무한리필 샤브샤브 1곳 추천")

    assert "편편집 목동사거리점" in response
    assert "포인트: 무한리필" in response
    assert "무제한" in response
    assert "여럿이 가기 편할 수 있습니다" in response
    assert store.item_count == 1


def test_service_logs_stage_timings_with_masked_chat_id(tmp_path: Path, caplog) -> None:
    logger = logging.getLogger("momukbot.test.service_timing")
    service = RecommendationService(settings(tmp_path), FakeAgent(), FakeSearch(), logger=logger)

    with caplog.at_level(logging.INFO, logger=logger.name):
        response = service.handle_text("1234567890", "서면에서 해장 국밥 추천해줘")

    assert response is not None
    messages = [record.getMessage() for record in caplog.records if record.name == logger.name]
    for stage in (
        "parse",
        "normalize",
        "search_context",
        "prompt_build",
        "agent_generate",
        "response_parse",
        "store",
        "format",
        "total",
    ):
        assert any(f"stage={stage}" in message for message in messages)
    assert any("chat_id=***7890" in message for message in messages)
    assert all("1234567890" not in message for message in messages)
    assert any("context_chars=" in message for message in messages)
    assert any("prompt_chars=" in message for message in messages)
    assert any("raw_chars=" in message for message in messages)


def test_service_hides_raw_agent_text_when_recommendation_json_fails(tmp_path: Path) -> None:
    service = RecommendationService(settings(tmp_path), RawTextAgent(), FakeSearch())
    response = service.handle_text("cli", "서면에서 해장 국밥 추천해줘")

    assert response == "추천 결과 형식을 정리하지 못했어요. 잠시 후 다시 시도해주세요."


def test_service_does_not_store_raw_response_by_default(tmp_path: Path) -> None:
    store = RecordingStore()
    service = RecommendationService(settings(tmp_path), FakeAgent(), FakeSearch(), store)  # type: ignore[arg-type]

    service.handle_text("cli", "서면에서 해장 국밥 추천해줘")

    assert store.raw_response == ""


def test_service_stores_raw_response_when_enabled(tmp_path: Path) -> None:
    store = RecordingStore()
    service = RecommendationService(
        settings(tmp_path, store_raw_response=True),
        FakeAgent(),
        FakeSearch(),
        store,  # type: ignore[arg-type]
    )

    service.handle_text("cli", "서면에서 해장 국밥 추천해줘")

    assert "search_keyword" in store.raw_response
