# 뭐먹봇 (momukbot)

네이버 블로그 검색과 AI 에이전트를 연결한 텔레그램 맛집 추천 봇입니다.

뭐먹봇은 Telegram에서 "서면에서 해장할 건데 국밥 감자탕 위주로 추천해줘"처럼 물어보면, Naver 블로그 후기와 지역 검색 결과를 참고해 지금 가기 좋은 맛집 후보를 카테고리별로 정리합니다.

개인 토큰이나 특정 실행 환경에 묶이지 않도록 Telegram, Naver API, AI 실행, 응답 정리, 호출량 관리를 각각 나눠 두었습니다. 작은 봇이지만 다른 사람이 자기 계정과 API 키로 실행해볼 수 있는 형태를 목표로 했습니다.

## 빠른 시작

```bash
git clone <repo-url>
cd momukbot
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
momuk init
momuk doctor
momuk recommend --area 서면 --topic "해장 국밥 감자탕" --dry-run
momuk recommend "서면에서 해장 국밥 추천해줘" --dry-run
momuk telegram
```

`momuk init`은 `.env.example`을 복사해 `.env`를 만듭니다. `.env`에는 본인의 키만 넣으면 됩니다.

```env
TELEGRAM_BOT_TOKEN=
TELEGRAM_ALLOWED_CHAT_IDS=
TELEGRAM_ADMIN_USER_IDS=

NAVER_CLIENT_ID=
NAVER_CLIENT_SECRET=
NAVER_DAILY_SOFT_LIMIT=24000

AGENT_PROVIDER=codex_cli
CODEX_BIN=codex
```

## 필요한 준비

- Telegram Bot Token: BotFather에서 새 봇을 만들고 token을 발급받습니다.
- Telegram Chat ID: `TELEGRAM_ALLOWED_CHAT_IDS`는 수동 허용 목록입니다. 비워두면 초기 설정 편의를 위해 모든 채팅에서 동작합니다.
- Telegram Admin User ID: 본인 user id를 `TELEGRAM_ADMIN_USER_IDS`에 넣으면 `/chatid`, `/set_momuk_room` 명령으로 momukbot 채팅방을 확인하거나 등록할 수 있습니다.
- Naver Search API: Naver Developers에서 검색 API client id/secret을 발급받습니다. 블로그 검색을 후기 근거로, 지역 검색을 장소 힌트로 사용합니다.
- Codex CLI: 본인 PC에 설치되고 로그인된 `codex` CLI를 사용합니다. 이 저장소에는 작성자의 Codex 계정이나 실행 경로가 들어있지 않습니다.

## 명령어

```bash
momuk init
momuk doctor
momuk recommend --area 서면 --topic "해장 국밥 감자탕" --dry-run
momuk recommend --area 서면 --topic "해장 국밥 감자탕"
momuk recommend "서면에서 해장 국밥 추천해줘" --dry-run
momuk rooms
momuk setup-telegram
momuk telegram-commands show
momuk telegram-commands sync
momuk quota
momuk telegram
```

- `init`: `.env.example` 기반으로 `.env` 생성
- `doctor`: Telegram, Naver, Codex CLI, 로컬 상태 디렉터리 점검
- `recommend`: CLI에서 추천 실행. `--area/--topic` 방식과 Telegram처럼 자연어 입력하는 방식을 모두 지원
- `rooms`: 등록된 momukbot Telegram 채팅방과 실제 허용 상태 확인
- `setup-telegram`: Telegram 설정 상태와 다음에 입력할 명령 안내
- `telegram-commands show`: Bot command menu 확인
- `telegram-commands sync`: Bot command menu를 `/chatid`, `/set_momuk_room`으로 동기화
- `quota`: Naver API soft limit 사용량 확인
- `telegram`: Telegram polling bot 실행

Telegram에서 관리자 user id가 설정된 사용자는 아래 명령을 사용할 수 있습니다.

- `/chatid`: 현재 채팅방의 id, 타입, 이름 확인
- `/set_momuk_room`: 현재 채팅방을 momukbot 채팅방으로 등록

등록된 momukbot 채팅방 정보는 `.local/state/telegram_rooms.json`에 저장됩니다. `/set_momuk_room`으로 등록된 방은 `TELEGRAM_ALLOWED_CHAT_IDS`에 없어도 자동으로 momukbot 허용 대상에 포함됩니다.

허용 방식은 두 가지를 합쳐서 봅니다.

- `TELEGRAM_ALLOWED_CHAT_IDS`: `.env`에 직접 적는 수동 허용 목록
- `/set_momuk_room`: Telegram에서 관리자 명령으로 저장하는 런타임 등록 목록

둘 중 하나라도 설정되어 있으면 해당 목록에 포함된 채팅방에서만 일반 맛집 요청을 처리합니다.

## 응답 정책

- 한 번의 요청에 최대 30개 후보를 추천합니다.
- 현재 시간 기준으로 영업 중이거나 영업 가능성이 높은 곳을 우선합니다.
- 네이버 블로그 근거를 우선합니다.
- 블로그 검색 결과는 최신성, 지역/주제 매칭, 방문 후기 표현, 영업시간 힌트, 광고 의심 표현을 기준으로 내부 점수를 계산해 참고 순서를 정합니다.
- 결과는 카테고리별로 묶어 출력합니다.
- 각 장소에는 근거 링크와 네이버 지도 검색 링크를 붙입니다.
- Naver API 키가 없거나 soft limit을 넘으면 죽지 않고 fallback 안내를 제공합니다.

## 구조

```text
Telegram
  -> chat.TelegramBot
  -> core.RecommendationService
  -> search.NaverSearchProvider
  -> agent.CodexCliAgent
  -> core.formatter
  -> Telegram
```

자세한 설계 의도는 [docs/architecture.md](docs/architecture.md)를 참고하세요.

## 보안

`.env`, sqlite, log, state 파일은 커밋하지 않습니다. 공개 전에는 아래 검사를 실행하세요.

```bash
scripts/preflight_public.sh
pytest
```

## 라이선스

MIT
