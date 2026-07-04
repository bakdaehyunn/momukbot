# 뭐먹봇 아키텍처

뭐먹봇은 범용 agent framework가 아니라 맛집 추천 봇입니다. 다만 작은 봇에서도 외부 채널, 검색 API, AI 실행, quota, 응답 포맷을 나눠두면 유지보수와 공개 배포가 쉬워지는지 확인하기 위해 다음 경계를 둡니다.

## 흐름

```text
사용자 Telegram 메시지
  -> TelegramBot
  -> RecommendationService
  -> LLMRequestParser
  -> HybridSearchProvider
     -> KakaoLocalCandidateProvider
     -> NaverBlogEvidenceProvider
  -> CodexCliAgent
  -> formatter
  -> Telegram 메시지
```

## 경계

- `chat`: Telegram polling, 채팅방 hard gate, `/nearby` 위치 버튼, callback 선택지, 긴 메시지 분할을 담당합니다.
- `core`: rule parser와 LLM request router로 사용자의 자연어 요청을 `ParsedRequest`로 정리합니다. 추천 단계에서는 prompt 생성, 추천 JSON 파싱, Kakao/Naver 검증 후보에 대한 LLM 평가 결과 reconciliation, 포맷팅, 구조화 이벤트 기록을 담당합니다.
- `search`: fallback 없는 역할 분리 검색을 담당합니다. Kakao Local은 장소 존재, 카테고리, 주소, 필수 Kakao 지도 URL을 확인합니다. Naver Blog는 Kakao 후보별 후기 근거만 수집하고 점수화합니다. 최종 컨텍스트는 두 조건이 모두 맞은 후보만 포함합니다.
- `agent`: 사용자의 로컬 AI 에이전트를 호출합니다. v1은 `codex_cli`만 구현하며, 검증된 후보의 의도 적합도, 근거 품질, 메뉴군, 사용 상황, 리스트 다양성 그룹을 평가합니다. 새 장소를 만들거나 검색 fallback으로 쓰지 않습니다.
- `storage`: 추천 기록 sqlite, Naver quota soft limit json, Telegram 등록 방 상태, 추천 이벤트 JSONL을 로컬에 저장합니다.

블로그 점수는 맛집 자체의 평점이 아니라 AI에게 먼저 보여줄 후기 근거를 고르는 기준입니다. Naver Search API가 별점, 리뷰 수, 실제 영업시간을 제공하지 않으므로 그런 값은 추정하지 않습니다.

## 추천 정책

- Kakao Local 후보가 없으면 추천하지 않습니다.
- Kakao `place_url`이 없는 후보는 추천 후보에서 제외합니다.
- Naver Blog 근거가 Kakao 후보 이름과 매칭되지 않으면 추천하지 않습니다.
- Kakao Local 실패를 Naver Local로 대체하지 않습니다.
- Naver Blog 근거 부족을 LLM 자체 검색이나 local-only 추천으로 대체하지 않습니다.
- 일부 후보만 Kakao Local + Naver Blog 검증을 통과하면 검증된 후보만 추천하고 부족한 개수를 안내합니다.

## 관측성

추천 요청마다 가능한 범위에서 `.local/logs/recommendation-events.jsonl`에 구조화 이벤트를 남깁니다.

기록하는 값은 request id, timestamp, masked chat id, parse source/reason, parsed intent/area/topic/count, Kakao 후보 수, Naver Blog evidence 수, 매칭 후보 수, 최종 추천 수, outcome/failure reason, 전체 및 주요 stage timing입니다.

raw Telegram chat id, raw 좌표, API key, token, secret, 원문 LLM 응답은 이벤트에 넣지 않습니다. 최근 이벤트는 `momuk events --limit 20`으로 확인할 수 있습니다.

## 공개 배포 원칙

- 개인 token, Naver secret, Codex 계정, 로컬 경로를 저장소에 넣지 않습니다.
- 사용자의 `codex` CLI를 `CODEX_BIN`으로 호출합니다.
- Telegram은 webhook이 아니라 polling으로 시작합니다. 도메인과 HTTPS 설정 없이 clone 후 실행하기 쉽게 하기 위함입니다.
- Naver API는 soft limit을 먼저 확인해 의도하지 않은 호출량 증가를 줄입니다.

## v1에서 제외한 것

- Codex Skill
- MCP server
- Docker 배포
- Telegram webhook
- 네이버 지도 즐겨찾기 자동 등록
- Naver Local fallback
