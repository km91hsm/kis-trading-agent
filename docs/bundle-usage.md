# 번들(실행파일) 사용법 — macOS · Windows

Python·uv 를 설치하지 않고 쓰는 배포본입니다. GitHub Releases 의 `trader-<버전>-macos-<arm64|x86_64>.zip`(macOS) 또는
`trader-<버전>-windows-x86_64.zip`(Windows) 을 받아 압축을 풉니다. 압축 안 최상위 `README.md` 가 이 문서입니다.

## 1. 폴더 구조

```
trader/                ← 이 폴더가 TRADER_HOME. 폴더째 다른 PC 로 복사해도 된다
  Trader.app           ← macOS: 더블클릭 = 메뉴바 아이콘 + 데몬 + 웹 UI + 브라우저 (Dock 에는 안 뜬다)
  trader               ← macOS: 터미널용 CLI (Trader.app 안의 실행파일로 가는 링크)
  TraderApp.exe        ← Windows: 더블클릭 = 작업표시줄 아이콘 + 데몬 + 웹 UI + 브라우저 (콘솔 창 없음)
  trader.exe           ← Windows: 터미널용 CLI
  _internal/           ← Windows 전용. 런타임·라이브러리·에이전트 프롬프트·웹·템플릿 (macOS 는 .app 안에 있다)
  README.md            ← 이 문서
  config/              ← settings·accounts·universe·schedule·sources·ml 등 .yaml (첫 실행 때 생성)
  secrets/.env         ← API 키 (첫 실행 때 WEB_TOKEN 만 든 채로 생김 → 키를 덧붙여 채운다)
  state/               ← 원장 DB·runs·logs·features·models (실행하며 생김, 백업 대상)
  docs/                ← 이 문서 (첫 실행 때 복사)
```

## 2. 준비물

1. **Claude Code CLI** — 번들에 들어 있지 않습니다. https://claude.ai/code 안내대로 `claude` 를 설치하고
   한 번 로그인(구독 인증)해 둡니다. 터미널에서 `claude --version` 이 나오면 됩니다.
2. **KIS Open API 앱키** (모의·실전), **DART**·**FRED** 키, **네이버 API HUB** 앱의 Client ID/Secret,
   **텔레그램** 봇 토큰·chat_id. 키 이름은 `secrets/.env.example` 을 따릅니다.
3. (선택) **세이브티커 수집** — 일반 Chrome 확장과 로컬 Native host 로 수집합니다. 아래 4절의 설치 절차를 보세요.
4. (선택) 번들은 scikit-learn 백엔드로 돕니다. LightGBM·PPO 백엔드는 소스 설치(`uv sync --extra lightgbm --extra rl`,
   macOS 는 `brew install libomp`)에서만 쓸 수 있고, 없으면 sklearn 으로 폴백합니다.
   소스 설치에서 LightGBM 으로 학습·승격한 모델(`trader ml models` 의 backend)은 번들이 로드하지 못합니다 —
   번들과 `state/` 를 공유하려면 소스 쪽도 sklearn 백엔드로 학습하세요.

## 3. 첫 실행

1. **압축 풀기** 뒤 운영체제별로 한 번만:
   - macOS: **먼저** 터미널에서 `xattr -dr com.apple.quarantine trader/` 를 실행합니다(압축 파일의 격리
     표시 제거). 그래도 "확인되지 않은 개발자" 경고가 뜨면 `Trader.app` 을 **우클릭 → 열기** 로 한 번 엽니다.
     격리 표시를 지우지 않고 '우클릭 → 열기' 만 하면 macOS 가 앱을 읽기 전용 임시 경로(App Translocation)에서
     돌려 설정·원장을 만들 수 없습니다 — 그때는 앱이 안내 알림을 띄우고 `state/logs/startup-error.log`
     (없으면 홈 폴더)에 남긴 뒤 종료합니다.
   - Windows: SmartScreen 경고("Windows의 PC 보호")에서 **추가 정보 → 실행** 을 한 번 누릅니다.
2. **더블클릭**: `Trader.app`(macOS) 또는 `TraderApp.exe`(Windows). 메뉴바(작업표시줄)에 ▲ 아이콘이 뜨고,
   처음에는 `config/`·`secrets/.env.example`·`docs/` 가 만들어지고 `secrets/.env` 에 웹 쓰기 토큰 `WEB_TOKEN` 이
   자동으로 들어간 뒤 **웹 UI**(http://127.0.0.1:8787)가 열립니다.
   키가 없으면 수집·세션은 실패하지만 UI 는 뜹니다. 터미널에서 같은 초기화만 하려면 `./trader init`
   (Windows: `trader.exe init`) — 이미 있는 파일은 건드리지 않습니다.
3. **키 넣기**: 첫 실행이 만든 `secrets/.env` 에 `secrets/.env.example` 의 키 이름대로 줄을 덧붙여 값을 채웁니다
   (`WEB_TOKEN` 줄은 자동 생성이니 그대로 둡니다 — `.env.example` 을 복사해 덮어쓰면 토큰이 사라집니다). 다른 프로젝트의 `.env` 가 있으면
   `./trader accounts secrets import --from <그 파일>` 로 이름 매핑 복사(값은 출력되지 않음).
4. **계좌**: `config/accounts.yaml` 의 `cano` 자리표시자(`00000000`)를 본인 계좌번호로 바꿉니다(모의는 `auto`,
   실전은 `off` 로 시작). `config/sources.yaml` 의 `edgar.user_agent`(또는 `.env` 의 `EDGAR_USER_AGENT`)에
   `<이름> <이메일>` 을 넣습니다(SEC 요구).
5. **점검**: `./trader accounts ping`(KIS 시세·계좌 연결), `./trader status`(`state/STATUS.md` 생성),
   `./trader selfcheck`(설치 점검 JSON 1줄).
6. **리허설**(구독 호출 없음) — macOS: `FAKE_CLAUDE=1 ./trader spot analyze --ticker 005930 --dry-run`,
   Windows PowerShell: `$env:FAKE_CLAUDE="1"; .\trader.exe spot analyze --ticker 005930 --dry-run`.
7. **자동 시작**(선택): `./trader install` 로 로그온 시 데몬 자동 시작을 등록합니다(해제 `--uninstall`,
   데몬 밖 5분 감시는 `--watchdog`).

## 4. 평소 운용

- 더블클릭(= `trader app --tray`) 하나로 스케줄 데몬 + 웹 UI 가 돕니다. 메뉴바(작업표시줄)의 ▲ 아이콘 메뉴:
  **웹 UI 열기 / 일시정지·재개 / 로그 폴더 열기 / 종료**. 종료는 실행 중인 분석 세션이 끝나길 기다린 뒤
  내려갑니다(최대 `llm.timeout_s`).
- 터미널에서 창과 함께 보고 싶으면 `./trader app`(트레이 없음, Ctrl+C 로 종료).
- 데몬 로그는 `state/logs/`. 외부 감시 1회는 `./trader watchdog`.
- 텔레그램: 알림 수신, `/approve <id>`·`/reject <id>`·`/status`·`/mode <계좌> <auto|approve|off>` 등.
- 웹 UI 제어(일시정지·승인·모드·작업 실행)는 쓰기 토큰(`secrets/.env` 의 `WEB_TOKEN`, 첫 실행 때 자동 생성)이 필요합니다.
  이 PC 에서 연 브라우저는 시스템 화면의 「이 PC 에서 받기」로 토큰을 받고, 폰·tailnet 에서는 그 값을 직접 붙여 넣습니다.
  토큰을 넣기 전의 브라우저는 조회만 됩니다(`WEB_TOKEN` 이 아예 없으면 서버가 쓰기를 모두 거부 — `trader web` 만 쓸 때).
- 작업은 터미널 없이 웹 대시보드에서, 또는 `./trader jobs run <작업>` 으로 데몬 큐에 넣을 수 있습니다
  (데몬이 켜져 있어야 함 — `./trader jobs kinds` 로 종류 확인).
- **세이브티커 Chrome 수집기 설치**: `./trader saveticker extension prepare --destination <새 패키지 경로>` 로
  검토용 패키지를 만들고(Chrome·backend 는 바꾸지 않음), `REVIEW.md`·`extension/manifest.json` 을 확인한 뒤
  `./trader saveticker extension install-host <패키지 경로>` 로 로컬 host 를 등록하고 `chrome://extensions`
  개발자 모드에서 패키지의 `extension/` 을 압축해제된 확장으로 로드합니다. 상태는
  `./trader saveticker extension status`, 제거는 확장을 지운 뒤 `./trader saveticker extension uninstall-host <패키지 경로>`.
  Chrome 에서 직접 로그인해야 하고 컴퓨터가 깨어 있어야 수집합니다. 저장 폴더는 `state/external/saveticker/`.
- 자주 쓰는 터미널 명령(`uv run trader …` 대신 `./trader …`):
  `status`, `scan run`, `spot analyze --ticker <코드>`, `spot monitor --once --dry-run`, `spot fills sync`, `spot approvals list`,
  `accounts show|pause|resume`, `ml features backfill --years 3`, `ml train`, `ml models`, `evaluation rl models`,
  `evaluation readiness`, `evaluation backtest --since YYYY-MM-DD`.

## 5. 업데이트·백업

- 새 zip 을 풀어 **`Trader.app`·`trader`(macOS) 또는 `_internal/`·실행파일 둘(Windows)만** 교체합니다.
  `config/ secrets/ state/` 는 그대로 둡니다. 버전은 `./trader --version`.
- 백업은 `config/ secrets/ state/` 세 폴더면 충분합니다. 배포 zip 에는 이 폴더들이 들어가지 않습니다.

## 6. 문제가 날 때

- "claude 실행파일을 찾을 수 없습니다": PATH 에 `claude` 가 없습니다. `config/settings.yaml` 의
  `claude_bin` 에 전체 경로를 적습니다.
- 웹이 안 열림: 터미널에서 `./trader web` 로 띄우고 오류 메시지를 봅니다. 포트는 `settings.yaml` `web.port`.
- 더블클릭해도 아무 반응이 없음: `state/logs/startup-error.log` 를 봅니다.
- 원장 오류: `state/trader.db` 가 깨진 경우 `state/` 를 백업 뒤 비우면 새로 만듭니다(기록 유실 주의).
