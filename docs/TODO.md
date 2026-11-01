# TODO — 마일스톤 체크리스트

정본은 스펙 §3. 각 M 은 브랜치 1개·PR 1개이고, 옛 테스트를 새 구조로 옮겨 통과해야 완료다.

## M0 — 뼈대
저장소·uv·ruff·CI·templates·`kernel/{config,platform,registry,ledger 코어,daemon 셸,web 셸,cli 루트}`·계약 테스트 선행. 완료 기준: CI 녹색, `trader init`·`selfcheck` 동작.
- [x] Task 1 저장소·pyproject·uv·ruff·`.gitignore`·CI 골격
- [x] Task 2 `kernel/platform`
- [x] Task 3 `kernel/config`·`templates/config/`
- [x] Task 4 `kernel/ledger` 코어
- [x] Task 5 `kernel/registry`(계약·import 경계 검사)
- [x] Task 6 `kernel/daemon` 셸
- [x] Task 7 `kernel/web` 셸·SSE 키 생성
- [x] Task 8 `web/` Vite 스캐폴드
- [x] Task 9 `kernel/cli`(`init`·`selfcheck`·`daemon`·`web`·`dev`)
- [x] Task 10 문서(`CLAUDE.md`·`docs/TODO.md`·`docs/architecture.md`·`README.md`)
- [x] Task 11 저장소 공개·PR·CI·머지

## M1 — kernel/broker
KIS auth/client/market/broker/ws/fake·`tr_ids`·교차 프로세스 레이트리미터. 완료 기준: `kis ping` 코드 경로 테스트 통과.
- [x] Task 1 커널 전제 — `Env`·`MARKET_TZ`·`fsutil.atomic_replace` (2e58fc9)
- [x] Task 2 `errors`·`_parse`·`tr_ids`·`ticks`·`models`·`creds` (cd5be40)
- [x] Task 3 `auth.py` — 토큰 캐시·접속키 (d94c203)
- [x] Task 4 `client.py`·`rate_store.py` + 테스트 공용 fixture (dc58a3f)
- [x] Task 5 `market.py` — 현물 시세·순위·휴장일·종목명 (6eddc8c)
- [x] Task 6 `broker.py` — `KisBroker` 잔고·주문·체결 (8667224)
- [x] Task 7 `ws.py` 프레임 헬퍼 + `fake.py`(현물) (fea6eb4)
- [x] Task 8 선물옵션 — `fo_codes`·`fo_master`·`fo.py`·`market_fo.py`·`FakeFoBroker` (2ce0719)
- [x] Task 9 `factory.py`·`ping.py` — 공유 디렉터리 override·클라이언트 캐시·점검 (8feb446, 7a1c743)
- [x] Task 10 문서·체크리스트·공개면 점검
- [x] PR·CI(ubuntu+windows)·머지

## M2 — features/accounts + notify
계좌 yaml·모드·LIVE 확인·키·추가삭제, `kernel/notify` 전송·outbox. 완료 기준: 옛 `control/*`·`notify` 테스트 이식 통과.
- [x] Task 1 `kernel/config` secrets 공개면
- [x] Task 2 `accounts` 설정 모델·템플릿
- [x] Task 3 `kernel/notify` telegram·messages
- [x] Task 4 `notify.commands` 디스패처·`DaemonDeps.notifier`
- [x] Task 5 `accounts` 마이그레이션·모드
- [x] Task 6 `notify-test`·`telegram poll|loop`
- [x] Task 7 `accounts.brokers`
- [x] Task 8 `accounts` CLI (show·mode·pause·resume·ping)
- [x] Task 9 `accounts_io`·`secrets_io`·add·remove·secrets import
- [x] Task 10 텔레그램 명령 `/pause /resume /mode`
- [x] Task 11 문서·체크리스트·공개면 점검
- [ ] PR·CI(ubuntu+windows)·머지
- 이연(M0 리뷰):
  - `contracts._default_roots` 동적 import 를 discover 경유로
  - `import_violations` 루트 추론을 명시 키워드로
  - bare `import trader.features.a` 가 서비스 기능 간에 허용되는 구멍 막기(+테스트)
  - `test_discover_real_package…` 실제 목록 단언
  - WEB_TOKEN 을 `secrets/.env` 에서도 읽기(python-dotenv)
- 이연(M2 리뷰, minor): `Boot.secrets` 타입 `Secrets | None`·`build_web_deps` 재읽기 제거; `settings.py` 자리표시자 판정 헬퍼화; `commands.py` echo 예외 집계(try/else)·미인식 명령 도움말 블록 중복; `notify.enabled=false` 와 토큰 없음 문구 구분; `modes.py` docstring; `telegram.py` `ctx: Any`; T1 `load_secrets` UnicodeDecodeError 바이트 위치 노출; T9 `accounts add` ValidationError 원문 echo 가독성; T10 `/mode … fast` 경로 단언 약함; T4 `/status` 절별 격리(M4 가 status 절을 더하기 전에)·`commands.registry` 검사를 `registry.contracts.check` 로 이동(M4)·데몬 telegram 잡 예외 내성(M4); 핸들러가 컨텍스트의 accounts/secrets 를 쓰도록
  - 상세 차이는 `features/accounts/README.md`·`docs/architecture.md` 「notify」 의 "알려진 차이"

## M3 — features/ingest + saveticker
수집(KIS·DART·EDGAR·뉴스·커뮤니티·거시)과 saveticker 수집기·리더·데몬 잡. 완료 기준: 옛 `sources/*`·`saveticker/*` 테스트 이식 통과.
- [ ] 착수 전
- 이연(M0 리뷰):
  - TODO 승계 `EDGAR_USER_AGENT` 입력 항목

## M4 — kernel/session + kernel/execution + spot analyze
SessionKind 레지스트리·FAKE·executor 코어·`features/spot` analyze 경로(가장 큼, 골든 회귀 1회 재기준). 완료 기준: `FAKE_CLAUDE=1 trader analyze --dry-run` 체크포인트 일치.
- [ ] 착수 전
- 이연(M0 리뷰):
  - 계약 테스트 2종 이연: agents 공통 규칙 패리티, `allowedTools==tool_command()`
  - `Feature.tier=="lane"` ↔ `LANES` 교차 검증
  - `runs.finish` 실패로 남는 `running` 행 기동 시 정리
  - 두 번째 시그널 테스트 `wait=True` 실동작
  - 게이트 공급 경로(`Feature` 에 gates 필드 또는 deps 주입) 설계
- 이연(M2): `expire_approvals` 훅·데몬 `telegram_poll` 잡·outbox flush 잡·`exec-key` CLI·`DEAD_STATUSES` 커널 이동·`accounts_io.blockers` 실제 스키마; 옛 `SCALP_EXIT` 표·`DecisionBrief` 를 `notify.messages` 에(M4/M5/M9)

## M5 — spot 운영 경로
monitor·resume·손절·fills·notice·reconcile·balance·reprice. 완료 기준: 옛 테스트 이식 통과.
- [ ] 착수 전
- 이연(M2): 체결통보 리스너(`_notice_listeners`)

## M6 — features/regime + 의도 생산자
국면·stance·시장 세션·킬스위치·블랙아웃, hedge·cash_park·rebalance, scan 의 테마·사건. 완료 기준: 옛 테스트 이식 통과.
- [ ] 착수 전

## M7 — features/scan + ml + jobs
quant·screener·discovery·유니버스 PIT, 피처·학습·레지스트리·랭커·rl, 작업 큐. 완료 기준: 옛 테스트 이식 통과.
- [ ] 착수 전
- 이연(M2): watchlist·`/watch`(옛 `test_control` watchlist 2건)

## M8 — features/evaluation + briefing
메모리·보정·reflect·섀도·성과·readiness·backtest·paper_review·일일 리포트, 아침 브리핑. 완료 기준: 옛 테스트 이식 통과.
- [ ] 착수 전

## M9 — features/scalp
단타 Lane. 완료 기준: 옛 `scalp/*`·`test_daemon_scalp` 이식 통과.
- [ ] 착수 전

## M10 — features/fo
파생 Lane(`LegStore`, 커널 executor 코어 위). 완료 기준: 옛 `fo/*` 계열 테스트 이식 통과.
- [ ] 착수 전
- [ ] 옛 tests/broker/test_fo_market.py::test_option_quote_iv_is_percent_and_reprice_converts 이식(trader.fo.pricing 의존, M1 에서 이월)

## M11 — features/quotes + 웹 API 12 자원 (백엔드만)
웹 API 12 자원·SSE 키 생성·계약 테스트. 완료 기준: 모든 자원 엔드포인트 테스트 통과, 옛 엔드포인트 대응표 100%. (M11b: 새 웹 UI 구현은 별도 브레인스토밍·플랜)
- [ ] 착수 전
- 이연(M0 리뷰):
  - `_hostname`/`_host_of` 중복 정리
  - Host userinfo 거부
  - `/api` catch-all 전 메서드 + 베어 `/api`
  - `dist` 존재 지연 검사
  - 웹 테스트 공백(경로 탈출·same-origin 포트·`_SHARED`)
  - `gen-sse-keys` 대상 경로·`web_dist()` 를 소스 트리 기준으로 정리
  - 비루프백 바인드 경고·`extra_hosts` 설정 연결
- 이연(M2): `ensure_web_token` 연결·`/api/accounts`·연결 키 라우트

## M12 — kernel/ops + 번들 + 전환
health·watchdog·status·retention, CLI 전체 점검, 번들(`packaging/build.py`·`build.yml`), `CLAUDE.md` 최종 다이어트, 전환 리허설. 완료 기준: `FAKE_CLAUDE=1 trader daemon --dry-run` 하루 무사고 → 사용자가 `install` 교체.
- [ ] 착수 전
- 이연(M0 리뷰):
  - `Lock` 주입 clock
  - 실제 시그널 핸들러 테스트 격리
  - `start()` 실패 시 `_hb.stop()`
  - `daemon` 명령 흐름 정리
  - `uvicorn` 지연 import
  - `test_bundle` 단언 보강
  - `selfcheck` 가 DB 를 만드는 점
  - CLAUDE.md 금지 명령 "(M<N> 이후)" 표기·"WAL+30초" 중복
  - Windows `install` 로그온 확인(승계)

## 사용자 확인 대기 (옛 저장소 승계)

옛 저장소에서 아직 미확인인 실기 항목. 새 저장소 해당 기능이 이식된 뒤 확인한다.

- [ ] 실기(사용자 확인 대기, 영업일 09:00~15:30 KST): KIS 주문 테스트 — `kis order-test --account paper-main` → 1주 지정가(현재가 −10%) 접수·취소 메시지 확인 (M1·M2 이후)
- [ ] 실기(사용자 확인 대기): 텔레그램 승인 — 모드를 `approve` 로 두고 BUY 결정 때 승인 요청 → 텔레그램 `/approve <id>` → 주문 접수 확인 → 모드 `auto` 원복 (M2·M4 이후)
- [ ] 사용자 확인 대기: `secrets/.env` 에 `WEB_TOKEN` 설정 후 `trader web` → 설정 페이지에 토큰 입력 → 일시정지/재개가 반영되는지, 승인 요청이 있을 때 승인 → 주문 접수 확인 (M11 이후)
- [ ] 실기(사용자 확인 대기): 번들 zip 을 풀어 더블클릭 → 웹 UI → `kis ping` (macOS 우클릭 열기·Windows SmartScreen 1회) (M12 이후)
- [ ] 실기(사용자 확인 대기): `Trader.app` 더블클릭 → 메뉴바 아이콘·브라우저 → 메뉴에서 일시정지/재개·로그 폴더·종료(실행 중 세션 대기 후 잠금 해제). Windows 는 `TraderApp.exe` 작업표시줄 아이콘·콘솔 창 없음 확인 (M12 이후)
- [ ] 실기(사용자 확인 대기, 모의): KR 잔고 응답 `output2.tot_evlu_amt`·`nass_amt`·종목 `ord_psbl_qty` 필드 존재, US 잔고·체결이 NASD·NYSE·AMEX 3회 조회로 합쳐지는지, 재부팅 뒤 `daemon.lock` 이 stale 로 넘겨지는지 (M5 이후)
