# 뭐먹봇 아키텍처

뭐먹봇은 범용 agent framework가 아니라 맛집 추천 봇입니다. 다만 작은 봇에서도 외부 채널, 검색 API, AI 실행, quota, 응답 포맷을 나눠두면 유지보수와 공개 배포가 쉬워지는지 확인하기 위해 다음 경계를 둡니다.

## 흐름

```text
사용자 Telegram 메시지
  -> TelegramBot
  -> RecommendationService
  -> NaverSearchProvider
  -> CodexCliAgent
  -> formatter
  -> Telegram 메시지
```

## 경계

- `chat`: Telegram polling과 메시지 분할만 담당합니다.
- `search`: Naver Search API 호출과 블로그/로컬 컨텍스트 생성을 담당합니다. 블로그 결과는 `postdate`, 제목/요약 키워드, 방문 후기 표현, 영업시간 힌트, 광고 의심 표현으로 근거 우선순위를 계산합니다.
- `agent`: 사용자의 로컬 AI 에이전트를 호출합니다. v1은 `codex_cli`만 구현합니다.
- `core`: 요청 파싱, 프롬프트 생성, 추천 JSON 파싱, 포맷팅을 담당합니다.
- `storage`: 추천 로그와 Naver quota soft limit을 로컬 sqlite/json에 저장합니다.

블로그 점수는 맛집 자체의 평점이 아니라 AI에게 먼저 보여줄 후기 근거를 고르는 기준입니다. Naver Search API가 별점, 리뷰 수, 실제 영업시간을 제공하지 않으므로 그런 값은 추정하지 않습니다.

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
- Naver 외 검색 provider 구현
