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
momuk setup
momuk doctor
momuk recommend --area 서면 --topic "해장 국밥 감자탕" --dry-run
momuk recommend "서면에서 해장 국밥 추천해줘" --dry-run
momuk telegram
```

로컬에서 한 번에 준비하려면 아래 스크립트를 사용할 수 있습니다.

```bash
scripts/setup.sh
```

`momuk setup`은 `.env`를 만들고 Telegram, Naver, Codex CLI 설정값을 입력받은 뒤 `doctor` 점검과 다음 검증 명령을 안내합니다. 기존 값이 있으면 Enter로 유지할 수 있고, token과 secret은 화면에 출력하지 않습니다.

```env
TELEGRAM_BOT_TOKEN=
TELEGRAM_ALLOWED_CHAT_IDS=
TELEGRAM_ADMIN_USER_IDS=
MOMUK_ALLOW_ALL_CHATS=false
MOMUK_STORE_RAW_RESPONSE=false

NAVER_CLIENT_ID=
NAVER_CLIENT_SECRET=
NAVER_DAILY_SOFT_LIMIT=24000

AGENT_PROVIDER=codex_cli
CODEX_BIN=codex
```

## 필요한 준비

- Telegram Bot Token: BotFather에서 새 봇을 만들고 token을 발급받습니다.
- Telegram Chat ID: `TELEGRAM_ALLOWED_CHAT_IDS`는 수동 허용 목록입니다. 비워두면 등록된 momukbot 채팅방에서만 동작합니다.
- Telegram Admin User ID: 본인 user id를 `TELEGRAM_ADMIN_USER_IDS`에 넣으면 `/chatid`, `/set_chat_room` 명령으로 momukbot 채팅방을 확인하거나 등록할 수 있습니다.
  전역 Telegram 메뉴에는 `/chatid`만 노출하고, `/set_chat_room`은 직접 입력하거나 등록된 뭐먹봇 방의 scoped 메뉴에서 사용합니다.
- 전체 채팅 허용: 초기 테스트 목적으로 모든 채팅을 허용해야 할 때만 `MOMUK_ALLOW_ALL_CHATS=true`를 명시합니다.
- Naver Search API: Naver Developers에서 검색 API client id/secret을 발급받습니다. 블로그 검색을 후기 근거로, 지역 검색을 장소 힌트로 사용합니다.
- Codex CLI: 본인 PC에 설치되고 로그인된 `codex` CLI를 사용합니다. 이 저장소에는 작성자의 Codex 계정이나 실행 경로가 들어있지 않습니다.

## 명령어

```bash
momuk init
momuk setup
momuk doctor
momuk recommend --area 서면 --topic "해장 국밥 감자탕" --dry-run
momuk recommend --area 서면 --topic "해장 국밥 감자탕"
momuk recommend "서면에서 해장 국밥 추천해줘" --dry-run
momuk rooms
momuk discover-chat
momuk send-test --chat-id <telegram-chat-id>
momuk setup-telegram
momuk telegram-commands show
momuk telegram-commands sync
momuk quota
momuk history clear --yes
momuk telegram
```

- `init`: `.env.example` 기반으로 `.env` 생성
- `setup`: `.env` 생성/수정, Telegram chat id 자동 탐색, command menu 동기화 선택, `doctor` 점검, 다음 검증 명령 안내
- `doctor`: Telegram, Naver, Codex CLI, 로컬 상태 디렉터리 점검
- `recommend`: CLI에서 추천 실행. `--area/--topic` 방식과 Telegram처럼 자연어 입력하는 방식을 모두 지원
- `rooms`: 등록된 momukbot Telegram 채팅방과 실제 허용 상태 확인
- `discover-chat`: bot이 받은 최근 업데이트에서 chat id, 이름, 타입 확인
- `send-test`: 명시한 대상에 Telegram 테스트 메시지 전송. 기본 자동 전송은 하지 않으며 `--chat-id`, `--registered`, `--allowed` 중 하나를 지정해야 함
- `setup-telegram`: Telegram 설정 상태와 다음에 입력할 명령 안내
- `telegram-commands show`: Bot command menu 확인
- `telegram-commands sync`: 전역 Bot command menu는 `/chatid`만 두고, 등록된 momukbot 채팅방에는 `/chatid`, `/set_chat_room` scoped menu를 동기화
- `quota`: Naver API soft limit 사용량 확인
- `history clear --yes`: 로컬 sqlite 추천 기록 삭제
- `telegram`: Telegram polling bot 실행

Telegram에서 관리자 user id가 설정된 사용자는 아래 명령을 사용할 수 있습니다.

- `/chatid`: 현재 채팅방의 id, 타입, 이름 확인
- `/set_chat_room`: 현재 채팅방을 이 봇의 사용 방으로 등록

초기 설정 중 bot이 최근 메시지를 받은 Telegram chat을 `TELEGRAM_ALLOWED_CHAT_IDS`에 저장할 수 있습니다. 또는 `momuk telegram`을 실행한 뒤 관리자 사용자가 `/set_chat_room`을 보내 런타임 등록 방식으로 설정할 수 있습니다.
등록된 momukbot 채팅방 정보는 `.local/state/telegram_rooms.json`에 저장됩니다. `/set_chat_room`으로 등록된 방은 `TELEGRAM_ALLOWED_CHAT_IDS`에 없어도 자동으로 momukbot 허용 대상에 포함됩니다.
이미 다른 방이 등록된 상태에서 새 방을 등록하려면 오등록 방지를 위해 `/set_chat_room confirm`을 한 번 더 보내야 합니다.

허용 방식은 두 가지를 합쳐서 봅니다.

- `TELEGRAM_ALLOWED_CHAT_IDS`: `.env`에 직접 적는 수동 허용 목록
- `/set_chat_room`: Telegram에서 관리자 명령으로 저장하는 런타임 등록 목록

둘 중 하나라도 설정되어 있으면 해당 목록에 포함된 채팅방에서만 일반 맛집 요청을 처리합니다.

둘 다 비어 있으면 일반 맛집 요청은 처리하지 않습니다. 테스트 목적으로 모든 채팅을 허용하려면 `.env`에 `MOMUK_ALLOW_ALL_CHATS=true`를 명시하세요.

기존 다른 봇에서 분리한 로컬 상태가 남아 `reminder_chat_id`와 `momuk_chat_id`가 같은 값이면 뭐먹봇은 해당 방을 유효한 등록 방으로 쓰지 않습니다. 이 경우 `.local/state/telegram_rooms.json`을 백업 후 정리하고, 올바른 Telegram 채팅방에서 `/set_chat_room`을 다시 실행하세요.

## macOS 자동 실행

Telegram polling bot을 launchd로 계속 실행하려면 아래 스크립트를 사용합니다.

```bash
scripts/install_launch_agent.sh
scripts/uninstall_launch_agent.sh
```

LaunchAgent는 `momuk telegram`을 `KeepAlive`로 실행합니다. 로그는 `.local/logs/telegram.stdout.log`, `.local/logs/telegram.stderr.log`에 기록됩니다.

## 응답 정책

- 한 번의 요청에 최대 30개 후보를 추천합니다.
- 현재 시간 기준으로 영업 중이거나 영업 가능성이 높은 곳을 우선합니다.
- Naver Local로 장소 존재/카테고리/주소를 먼저 확인하고, 매칭되는 네이버 블로그 근거가 있는 후보만 추천합니다.
- 넓은 블로그 검색에서 Local 후보 매칭이 부족하면 상위 미매칭 후보 일부를 `지역 + 장소명 + 후기`로 추가 확인합니다.
- 블로그 검색 결과는 최신성, 지역/주제 매칭, 방문 후기 표현, 영업시간 힌트, 광고 의심 표현을 기준으로 내부 점수를 계산하고, 후보당 상위 근거를 LLM 평가 컨텍스트로 넘깁니다.
- LLM은 검증된 후보 안에서 사용자 원문 의도, 근거 품질, 메뉴군, 사용 상황, 리스트 다양성 그룹을 평가하고 재정렬하며, 새 후보 생성이나 외부 검색 fallback으로 쓰지 않습니다.
- 최종 추천 항목은 이번 요청의 Naver API context 안에서 확인된 네이버 블로그 링크가 있어야 합니다.
- Naver Local 후보는 장소 존재 확인 보조 정보로만 사용하며, 블로그 확인 없이 최종 추천을 채우지 않습니다.
- 블로그 근거가 확인된 후보가 요청 개수보다 적으면 확인된 개수만 응답하고, 확인되지 않은 후보는 제외합니다.
- 결과는 카테고리별로 묶어 출력합니다.
- 각 장소에는 참고 가능한 네이버 블로그 링크와 Naver Local 주소, 네이버 지도 링크를 붙입니다.
- Naver API 키가 없거나 soft limit을 넘으면 LLM 자체 검색으로 대체하지 않고 안내 메시지를 반환합니다.

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

기본값은 AI 원문 응답을 sqlite에 저장하지 않습니다. 디버깅 목적으로 원문 저장이 필요할 때만 `.env`에 `MOMUK_STORE_RAW_RESPONSE=true`를 설정하세요.

```bash
scripts/preflight_public.sh
pytest
```

## 라이선스

MIT
