# 설치 안내 — macOS · Windows

Python·uv 없이 쓰는 설치판입니다. 제품 개요와 사용법은 [사용자 설명서](user-guide.md)에 있습니다. [GitHub Releases](https://github.com/km91hsm/kis-trading-agent/releases) 에서
운영체제에 맞는 파일 하나를 받습니다.

| 운영체제 | 파일 |
|---|---|
| macOS (Apple 실리콘) | `trader-<버전>-macos-arm64.dmg` |
| macOS (Intel) | `trader-<버전>-macos-x86_64.dmg` |
| Windows (64비트) | `trader-<버전>-windows-x86_64-setup.exe` |

앱과 데이터는 따로 있습니다. 앱을 지우거나 새 버전으로 바꿔도 **데이터 폴더**는 그대로 남습니다.

| | macOS | Windows |
|---|---|---|
| 앱 | `/Applications/Trader.app` | `%LOCALAPPDATA%\Programs\Trader\` |
| 데이터 폴더 | `~/Library/Application Support/Trader` | `%LOCALAPPDATA%\Trader` |
| 터미널 CLI | `/Applications/Trader.app/Contents/MacOS/trader` | `%LOCALAPPDATA%\Programs\Trader\trader.exe` |

데이터 폴더 안: `config/`(설정 yaml) · `secrets/.env`(API 키) · `state/`(원장 DB·로그·모델·수집 파일) ·
`docs/`(이 문서) · `saveticker-extension/`(Chrome 확장 복사본).

## 1. 준비물

1. **Claude Code CLI** — 설치판에 들어 있지 않습니다. https://claude.ai/code 안내대로 `claude` 를 설치하고 한 번
   로그인(구독 인증)해 둡니다. 터미널에서 `claude --version` 이 나오면 됩니다.
2. **KIS Open API 앱키**(모의·실전), **DART**·**FRED** 키, **네이버 API HUB** 앱의 Client ID/Secret,
   **텔레그램** 봇 토큰·chat_id. 키 이름은 데이터 폴더의 `secrets/.env.example` 을 따릅니다.
3. (선택) 세이브티커 수집용 **Chrome**(6절).
4. 설치판은 scikit-learn 백엔드로 돕니다. LightGBM·PPO 백엔드는 소스 설치(`uv sync --extra lightgbm --extra rl`)에서만
   쓸 수 있고 없으면 sklearn 으로 폴백합니다. 소스 설치에서 LightGBM 으로 학습·승격한 모델은 설치판이 로드하지 못합니다.

## 2. 설치

### macOS

1. `.dmg` 를 더블클릭해 엽니다.
2. 창 안의 **Trader** 를 옆의 **Applications(응용 프로그램)** 폴더로 끌어다 놓습니다. 그다음 디스크 이미지는
   꺼내도 됩니다(Finder 사이드바의 ⏏). 디스크 이미지 안에서 바로 실행하지 마세요 — 세이브티커 확장 설치가 거부됩니다.
3. 첫 실행 때 "확인되지 않은 개발자" 경고가 뜹니다(코드 서명이 없는 앱). **응용 프로그램 폴더에서 Trader 를
   우클릭 → 열기 → 열기** 를 한 번 누릅니다. macOS 15 이상에서 '열기' 버튼이 없으면 **시스템 설정 → 개인정보 보호 및
   보안** 맨 아래의 「그래도 열기」를 누릅니다. 이후에는 평소처럼 더블클릭으로 열립니다.

### Windows

1. `…-setup.exe` 를 실행합니다. SmartScreen 경고("Windows의 PC 보호")가 뜨면 **추가 정보 → 실행** 을 누릅니다.
2. 관리자 권한은 필요 없습니다(현재 사용자에게만 설치). 마지막 화면의 「Trader 실행」을 체크한 채 마치면 바로 켜집니다.
   시작 메뉴에 **Trader** 가 생깁니다.

## 3. 첫 실행 · 키 넣기

1. **Trader 실행** — 메뉴바(macOS) 또는 작업표시줄 알림 영역(Windows)에 ▲ 아이콘이 뜹니다(Dock 에는 안 뜹니다).
   처음 실행하면 데이터 폴더에 `config/`·`secrets/.env.example`·`docs/` 가 생기고, `secrets/.env` 에 웹 설정 변경 인증키
   `WEB_TOKEN` 이 자동으로 들어간 뒤 **웹 UI**(http://127.0.0.1:8787)가 열립니다. 키가 없으면 수집·세션은 실패하지만
   UI 는 뜹니다.
2. **데이터 폴더 열기** — ▲ 아이콘 메뉴의 「데이터 폴더 열기」를 누릅니다.
3. **키 넣기** — `secrets/.env` 를 텍스트 편집기로 열고 `secrets/.env.example` 의 키 이름대로 줄을 덧붙여 값을
   채웁니다. `WEB_TOKEN` 줄은 그대로 둡니다(`.env.example` 을 복사해 덮어쓰면 토큰이 사라집니다). 다른 프로젝트의
   `.env` 가 있으면 `trader accounts secrets import --from <그 파일>` 로 이름 매핑 복사(값은 출력되지 않음).
4. **계좌** — `config/accounts.yaml` 의 `cano` 자리표시자(`00000000`)를 본인 계좌번호로 바꿉니다(모의는 `auto`,
   실전은 `off` 로 시작). `config/sources.yaml` 의 `edgar.user_agent`(또는 `.env` 의 `EDGAR_USER_AGENT`)에
   `<이름> <이메일>` 을 넣습니다(SEC 요구).
5. 설정을 바꿨으면 ▲ 메뉴 「종료」 후 다시 실행합니다.

## 4. 터미널 CLI

설치판에도 터미널용 `trader` 가 들어 있습니다. 경로가 길어서 별칭을 두면 편합니다.

- macOS (`~/.zshrc` 에 한 줄):
  ```
  alias trader="/Applications/Trader.app/Contents/MacOS/trader"
  ```
- Windows PowerShell (`$PROFILE` 에 한 줄):
  ```
  Set-Alias trader "$env:LOCALAPPDATA\Programs\Trader\trader.exe"
  ```

설치판의 `trader` 는 언제나 위 데이터 폴더를 씁니다(환경변수 `TRADER_HOME` 이 있으면 그쪽).
자주 쓰는 명령: `trader status`(`state/STATUS.md` 생성), `trader selfcheck`(설치 점검 JSON 1줄),
`trader accounts ping`(KIS 연결 점검), `trader --version`.
리허설(구독 호출 없음) — macOS: `FAKE_CLAUDE=1 trader spot analyze --ticker 005930 --dry-run`,
Windows PowerShell: `$env:FAKE_CLAUDE="1"; trader spot analyze --ticker 005930 --dry-run`.

## 5. 평소 운용 · 자동 시작

- 앱 하나(▲ 아이콘)가 스케줄 데몬 + 웹 UI 를 함께 돌립니다. 메뉴: **웹 UI 열기 / 일시정지·재개 / 로그 폴더 열기 /
  데이터 폴더 열기 / 로그인 시 자동 시작 / 종료**. 종료는 실행 중인 분석 세션이 끝나길 기다린 뒤 내려갑니다
  (최대 `llm.timeout_s`).
- **자동 시작**: 메뉴의 「로그인 시 자동 시작」을 체크하면 로그인할 때 데몬 + 웹 UI 가 브라우저 없이, 메뉴바(작업표시줄) ▲ 아이콘과 함께 뜹니다
  (터미널에서는 `trader install`, 해제 `trader install --uninstall`, 데몬 밖 5분 감시는 `--watchdog`).
  이미 떠 있는 상태에서 다시 실행하면 「이미 실행 중입니다」 안내 뒤 브라우저만 엽니다.
- 웹 UI 제어(일시정지·승인·모드·작업 실행)는 설정 변경 인증키(`WEB_TOKEN`)가 필요합니다. 앱이 실행 중인 PC의 로컬 주소에서 연
  브라우저는 **설정·운영 → 연결·설정 → 설정 변경 인증 → 이 PC 에서 받기**로 등록합니다. 다른 주소·브라우저에는 자동 공유되지 않습니다.
  폰·tailnet 주소에서는 이미 보유한 인증키를 직접 등록하거나, PC의 로컬 화면에서 변경합니다. 키가 저장돼 있어도 서버가 허용하지 않으면 요청은 거부됩니다.
- 텔레그램: 알림 수신, `/approve <id>`·`/reject <id>`·`/status`·`/mode <계좌> <auto|approve|off>` 등.
- 작업은 웹 대시보드에서, 또는 `trader jobs run <작업>` 으로 데몬 큐에 넣습니다(`trader jobs kinds` 로 종류 확인).

## 6. 세이브티커 수집(선택)

세이브티커는 Chrome 확장 + 로컬 Native host 로만 수집합니다(자동 브라우저 수집은 없음). 재확인·로그아웃에도 멈추지 않게 하려면 시스템 › 관리 › 연결 키 「세이브티커」 에 이메일·비밀번호를 넣고 팝업의 **재확인 시 계속 복구** 를 켭니다([사용자 설명서 4-5](user-guide.md#4-5-세이브티커-수집선택)).

1. 앱을 응용 프로그램 폴더(macOS) 또는 설치 경로(Windows)에 둔 상태로 실행하고, 웹 화면 맨 위 첫 안내의
   **설치하기**(또는 시스템 › 기능 › 데이터 수집의 **설치/갱신**)를 누릅니다(다시 해도 됨). 확장을 데이터 폴더의
   `saveticker-extension/extension` 으로 복사하고 설치된 `trader` 를 Chrome Native host 로 등록합니다. 터미널의
   `trader saveticker extension install` 도 같습니다(macOS 설치판은 `/Applications/Trader.app/Contents/MacOS/trader`).
2. Chrome 주소창에 `chrome://extensions` → 오른쪽 위 **개발자 모드** 켜기 → (다른 경로로 로드된 옛 Trader 수집
   확장이 있으면 먼저 삭제) → **압축해제된 확장 프로그램 로드** → 화면의 **경로 복사** 로 복사한 `<데이터 폴더>/saveticker-extension/extension` 선택.
3. Chrome 에서 세이브티커에 직접 로그인한 뒤 확장 팝업에서 **시작** 을 누릅니다.
4. Chrome 의 다운로드 위치는 기본값(사용자 `Downloads` 폴더)이어야 합니다 — 「다운로드 전 저장 위치 확인」도 끕니다.
   첫 PDF 를 저장할 때 macOS 가 `trader` 의 다운로드 폴더 접근 허용을 물으면 **허용** 합니다.
5. 상태는 `trader saveticker extension status`, 수집 결과는 `trader saveticker status`. 저장 폴더는
   데이터 폴더의 `state/external/saveticker/`. 컴퓨터가 깨어 있고 Chrome 이 켜져 있어야 수집합니다.
6. 제거: 팝업에서 중지 → `trader saveticker extension uninstall` → `chrome://extensions` 에서 확장 삭제.

## 7. 업데이트

1. 세이브티커를 쓰면 확장 팝업에서 **수집 중지**.
2. ▲ 메뉴 「종료」.
3. 새 버전 설치:
   - macOS: 새 `.dmg` 를 열고 Trader 를 응용 프로그램으로 다시 끌어다 놓아 **대치** 합니다.
   - Windows: 새 `…-setup.exe` 를 실행합니다(같은 위치에 덮어씁니다. 앱이 켜져 있으면 닫기를 묻습니다).
4. 앱을 실행합니다. 확장 복사본이 새 버전으로 바뀌면 "확장을 새로고침하세요" 알림이 한 번 옵니다 — 그때
   `chrome://extensions` 에서 Trader 수집 확장의 새로고침(↻)을 누르고 팝업에서 다시 시작합니다.

데이터 폴더(설정·키·원장)는 그대로 쓰입니다. 버전은 `trader --version`.

**0.3.0 에서 올릴 때 한 번만**: 0.3.0 의 자동 시작은 ▲ 아이콘 없이 떠서 2번의 「종료」를 쓸 수 없습니다. 3번까지 마친 뒤
터미널에서 `/Applications/Trader.app/Contents/MacOS/trader install`(감시도 쓰면 `… install --watchdog`)을 실행하세요.
등록이 `app --tray --no-browser` 로 바뀌면서 옛 프로세스를 내리고 새 버전을 ▲ 아이콘과 함께 띄웁니다.

## 8. 옛 휴대용 폴더에서 넘어오기

0.2.x 까지의 zip 판(압축을 푼 `trader/` 폴더 옆에 `config/ secrets/ state/` 가 있던 방식) 또는 소스 폴더에서
옮겨 올 때 한 번만 합니다. 원본은 지우지 않습니다.

1. 옛 데몬 내리기: 옛 트레이 앱 「종료」, 자동 시작을 등록했었다면 옛 `trader install --uninstall`
   (macOS 에서 직접 등록한 경우 `launchctl bootout gui/$(id -u)/com.trader.daemon`). 세이브티커 팝업에서 수집 중지.
2. 새 앱을 설치합니다(2절). 아직 실행하지 않아도 됩니다.
3. 미리 보기:
   ```
   trader migrate-home --from <옛 폴더> [--saveticker-from <옛 세이브티커 수집 폴더>] --dry-run
   ```
4. 결과가 맞으면 `--dry-run` 을 빼고 다시 실행합니다(원장은 SQLite backup 으로 복사, 잠금 파일 제외).
   새 데이터 폴더에 이미 원장이 있거나 데몬이 실행 중이면 거부합니다.
5. 터미널에서 새 앱의 `trader install`(감시도 쓰면 `trader install --watchdog`)을 실행해 옛 등록을 새 경로로 덮어씁니다.
   옛 등록 파일이 남아 있으면 ▲ 메뉴의 「로그인 시 자동 시작」이 이미 켜진 것으로 보여, 메뉴로 하면 오히려 해제됩니다.
6. 세이브티커를 쓰면 `trader saveticker extension install` 을 다시 하고 6절 2~3 단계대로 새 경로의 확장을 로드합니다.
7. 며칠 써 보고 문제가 없으면 옛 폴더를 정리합니다.

## 9. 백업 · 제거

- **백업**: 데이터 폴더 하나면 충분합니다(특히 `config/ secrets/ state/`). 앱은 언제든 다시 설치할 수 있습니다.
- **제거**:
  - macOS: 세이브티커를 썼으면 팝업에서 중지 후 `trader saveticker extension uninstall` → ▲ 메뉴에서
    「로그인 시 자동 시작」 해제 → 「종료」 → 응용 프로그램의 Trader 를 휴지통으로.
  - Windows: 세이브티커 팝업에서 수집 중지 → ▲ 메뉴 「종료」 → 설정 → 앱 → 설치된 앱(또는 제어판 → 프로그램 제거) → Trader.
    제거 프로그램이 로그온 자동 시작과 세이브티커 Native host 등록도 해제합니다(확장이 연결 중이면 등록 해제는
    건너뛰니, 남았으면 `chrome://extensions` 에서 확장을 지우면 됩니다).
  - 어느 쪽이든 **데이터 폴더는 남습니다.** 완전히 지우려면 데이터 폴더를 직접 삭제합니다(되돌릴 수 없음).

## 10. 문제가 날 때

- **실행해도 아이콘이 안 뜸**: 데이터 폴더의 `state/logs/startup-error.log` 를 봅니다(데이터 폴더를 만들 수 없을 때는
  홈 폴더의 `startup-error.log`). macOS 에서 "앱이 임시 위치(App Translocation)에서 실행 중" 안내가 뜨면 Trader 를
  응용 프로그램 폴더로 옮긴 뒤 다시 실행합니다.
- **"claude 실행파일을 찾을 수 없습니다"**: PATH 에 `claude` 가 없습니다. `config/settings.yaml` 의 `claude_bin` 에
  전체 경로를 적습니다.
- **웹이 안 열림**: 터미널에서 `trader web` 으로 띄우고 오류 메시지를 봅니다. 포트는 `settings.yaml` 의 `web.port`.
- 데몬 로그는 데이터 폴더의 `state/logs/`(▲ 메뉴 「로그 폴더 열기」). 외부 감시 1회는 `trader watchdog`.
- **원장 오류**: `state/trader.db` 가 깨지면 `state/` 를 백업한 뒤 비우면 새로 만듭니다(기록 유실 주의).
