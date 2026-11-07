# kis-trading-agent

한국투자증권(KIS) OpenAPI 기반의 LLM 멀티 에이전트 + ML 주식 자동매매 시스템입니다. 파이썬이 리스크 판정과 주문을 강제하고, LLM 은 도구만 호출합니다.

구현·운영 단계와 검증 결과는 [진행 상황](docs/TODO.md)을 참고하세요.

시장 자료의 출처, 거래대금 계산 범위, 웹 설정 및 Claude·Codex 토론은 [운영 안내](docs/market-data-codex.md)에 정리했습니다.

## 설치

### 설치판 (macOS · Windows)

[Releases](https://github.com/km91hsm/kis-trading-agent/releases) 에서 macOS 는 `trader-<버전>-macos-<arm64|x86_64>.dmg`
(Trader 를 응용 프로그램으로 끌어다 놓기), Windows 는 `trader-<버전>-windows-x86_64-setup.exe`(관리자 권한 불필요)를
받아 설치합니다. 데이터는 앱과 분리된 `~/Library/Application Support/Trader` · `%LOCALAPPDATA%\Trader` 에 생깁니다.
제품 개요·처음 설정·사용법(웹 UI·트레이·텔레그램·명령)은 [사용자 설명서](docs/user-guide.md), 첫 실행·키 넣기·업데이트·옛 폴더에서 넘어오기는 [설치 안내](docs/bundle-usage.md) 를 보세요.

### 소스

Python 3.12 와 [uv](https://docs.astral.sh/uv/) 가 필요합니다.

```
uv sync
uv run trader init
```

`init` 은 `config/ secrets/ state/ docs/` 를 만들고 `templates/config/` 의 자리표시자 파일을 `config/` 로 복사합니다. 실제 값은 직접 채웁니다.

## 실행

```
uv run trader selfcheck          # 환경·원장·기능 목록 점검 (JSON 1줄)
uv run trader daemon --dry-run   # 스케줄러 데몬
uv run trader web                # 웹 UI·API 서버
```

## 구조

`src/trader/kernel/` 은 기능 이름을 모르는 공용 뼈대(설정·원장·데몬·웹·CLI·기능 레지스트리)이고, `src/trader/features/<x>/` 가 기능 단위 수직 모듈입니다. 기능은 `feature.py` 의 `FEATURE` 한 곳에서 자신을 선언합니다.

- 설계 스펙: [docs/superpowers/specs/2026-11-02-kis-trading-agent-replatform-design.md](docs/superpowers/specs/2026-11-02-kis-trading-agent-replatform-design.md)
- 커널 모듈 메모: [docs/architecture.md](docs/architecture.md)
- 진행 상황: [docs/TODO.md](docs/TODO.md)
- 개발 규칙: [CLAUDE.md](CLAUDE.md)

## 보안

저장소는 공개입니다. `config/`(실제 계좌번호)·`secrets/`·`state/` 는 `.gitignore` 대상이며 커밋하지 않습니다. `templates/` 에는 자리표시자만 둡니다.

## 라이선스

MIT — [LICENSE](LICENSE)
