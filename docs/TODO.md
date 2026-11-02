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
- [x] Task 1: 커널 전제(의존성·paths·bundle·run_store·hooks·DaemonDeps·teardown·디스플레이 skip)
- [x] Task 2: saveticker 설정·경로·모델·스토어·보고서
- [x] Task 3: saveticker 파서·플랜·API 계약·네이티브 호스트
- [x] Task 4: saveticker Chromium·브라우저·감독·자동화
- [x] Task 5: saveticker 확장 설치·`extensions/saveticker/`·JS 테스트 CI
- [x] Task 6: saveticker 리더·CLI·데몬 잡·feature·api·README
- [x] Task 7: ingest 설정(sources·universe·sectors)·템플릿·원장 v3·base
- [x] Task 8: ingest 뉴스·뉴스 저장소·공시 중요도
- [x] Task 9: ingest DART·EDGAR·거시
- [x] Task 10: ingest 커뮤니티·채널(네이버 블로그·텔레그램·YouTube)
- [x] Task 11: ingest 수동 입력·기술 지표·세이브티커 적재
- [x] Task 12: ingest `build_inputs`·클라이언트 조립·CLI `ingest collect`
- [x] Task 13: ingest 데몬 잡 `news_collect`·api·feature 완성·README
- [x] Task 14: 문서·체크리스트·공개면 점검
- [x] PR #4 머지
- 이연(M0 리뷰):
  - TODO 승계 `EDGAR_USER_AGENT` 입력 항목 (아래 「사용자 확인 대기」)
- 이연(M3):
  - M4: `InputDeps.market` 주입, native-host argv 우회(번들) — `test_native_origin_routes_before_cli`, `trader collect` 별칭 여부
  - M6: `_vix`
  - M7: `_news_trigger`·`_discover_body`·`_disclosure_trigger`·`_events_check`·`ticker_names` 캐시·`peer_fn`·`universe check|sectors`, 옛 `test_daemon_news` 7건·`test_daemon_disclosure` 3건
  - M11: `/api/news`·`/api/control/manual-inputs`·`/api/system/saveticker`·restart
  - M12: health `disclosure`·`saveticker`, selfcheck playwright 점검, `test_health_disclosure_area_critical_for_held`, 패키징(`extensions/saveticker` data·playwright 드라이버)

## M4 — kernel/session + kernel/execution + spot analyze
SessionKind 레지스트리·FAKE·executor 코어·`features/spot` analyze 경로(가장 큼, 골든 회귀 1회 재기준). 완료 기준: `FAKE_CLAUDE=1 trader spot analyze --dry-run` 체크포인트 일치.
- [x] 착수
- [x] Task 1 커널 계약·설정·부트 확장 — `AgentDef`·`Port`·lane·`tool` 루트
- [x] Task 2 `kernel/session` 원장 v4·`calls`·`usage`
- [x] Task 3 `session.agents`(md 로더·`agents_json`·`orchestrator_prompt`)·`anonymize`·공통 규칙 계약 테스트
- [x] Task 4 `session.runner`(`SessionSpec`·`run_session`·`build_command`·stream 파싱)
- [x] Task 5 `kernel/execution` 원장 v5·`types`·`idem`·`ledger` 원시
- [x] Task 6 `execution.core`(transmit·승인·만료·지문)·notify 주문 문구
- [x] Task 7 spot 골격(설정·스키마·원장 v6·결정 저장소)·accounts/ingest 포트
- [x] Task 8 spot 게이트(rules·stops·horizon·costs·tradability)
- [x] Task 9 spot summary(수렴 감시·합의 토론 생략)
- [x] Task 10 spot 도구(`tools`·`tool_cli`·`StageRule` 표)
- [x] Task 11 spot `executor`·`submit-decision`·승인 래퍼·`exec-key` CLI
- [x] Task 12 spot analyze 세션(agents md·FAKE·`SessionKind`·`trader spot analyze`·`tool collect`)
- [x] Task 13 승인 경로(`trader spot approvals`·텔레그램 `/approve`·`/reject`·`approvals_expire` 잡)
- [x] Task 14 골든 회귀 재기준·analyze 체크포인트 집합 테스트
- [x] Task 15 문서·체크리스트·공개면 점검
- [x] PR #5 머지
- 이연(M0 리뷰, M4 가 한 것): 계약 테스트 2종(agents 공통 규칙 패리티, `allowedTools==tool_command()`)·`Feature.tier=="lane"` ↔ `LANES` 교차 검증
- 이연(M0 리뷰, 남음):
  - 두 번째 시그널 테스트 `wait=True` 실동작 (M5)
  - 게이트 공급 경로(`Feature.gates` 필드 또는 deps 주입) 설계 (M5)
  - `runs`/`agent_calls` 의 stale `running` 행 기동 시 정리 (M5)
- 이연(M2, M4 가 한 것): `exec-key` CLI(`trader spot exec-key list|release`), `expire_approvals` → `approvals_expire` 잡
- 이연(M2, 남음): `telegram_poll` 데몬 잡 (M5), outbox flush 잡 (M5), `accounts_io.blockers` 실제 스키마(orders·approvals·positions_snapshot, M5), `DEAD_STATUSES` 커널 이동·`SCALP_EXIT` 표(M5/M9)
- 이연(M4):
  - M5: `resume` 세션·`31_decision_r<n>` 재평가(옛 `stop_floor` 규칙 포함)·trigger 가격·stop·monitor·`telegram_poll`/outbox flush 잡·`holiday_fn`·기동 시 stale `running` 행 정리·게이트 공급 경로(`Feature.gates`)·시그널 `wait=True` 테스트·`accounts_io.blockers` 실제 스키마
  - M6: killswitch·blackout·hedge·park·regime 컨텍스트·승인 재판정의 bear/killswitch 재검사·`test_rules` 의 regime 호출자
  - M7: quant·RL·theme (`AnalystRole` 의 "quant" 복원 포함)
  - M8: memory·calibration·`quick.py`(`ask_json`·`probe_usage`)·`test_quick` 9건·결과 dict `memory_entry_id`·`memory_entries` 단언
  - M10: fo 세션·`test_session_fo`·`test_schemas_fo`·`test_ticks_fo`·`test_usage_fo`
  - M11: `decision_trace`
  - M12: `trader status`·`test_status_shows_last_decisions`·`paths.agents_dir` 정리·native-host argv 우회(번들)·`test_native_origin_routes_before_cli`
  - Ruling: `trader collect`·`trader analyze` 최상위 별칭은 두지 않는다(기능 sub-app 일관: `trader spot analyze`, `trader ingest collect`)

## M5 — spot 운영 경로
monitor·resume·손절·fills·notice·reconcile·balance·reprice. 완료 기준: 옛 테스트 이식 통과.
- [x] 착수
- [x] Task 1 원장 v7·v8·v9 + 원시 함수 + `core.cancel`
- [x] Task 2 `config.market_hours` — US 휴장·조기폐장·장중 창·`holiday_fn` 공급자
- [x] Task 3 `Feature.gates` 공급 경로·`DaemonDeps.ports`·bootstrap 훅·stale `running` 정리
- [x] Task 4 잡 큐 저장소(`job_requests`) — enqueue·claim·finish·cancel·stale·expire
- [x] Task 5 큐 틱 4레인·heavy 락·재기동 복구·캐치업·`trader jobs`
- [x] Task 6 내장 `telegram_poll` 잡(outbox flush)·운영 알림 문구
- [x] Task 7 fills 동기화·`fills_sync` 잡·cron `schedule_key` 리스트
- [x] Task 8 balance 스냅샷 잡·reconcile 대사
- [x] Task 9 resume 세션·`31_decision_r<n>`·`stop_floor`·트리거 가격 게이트
- [x] Task 10 monitor 순수 규칙(손절·익절·급등락·무효화·정기)
- [x] Task 11 `monitor_once`·손절 집행·`trader spot monitor`
- [x] Task 12 아침 미체결 재호가(reprice)
- [x] Task 13 체결통보 리스너(`notice`)
- [x] Task 14 큐 `reevaluate` 요청·종목 락
- [x] Task 15 문서·템플릿·체크리스트·골든 재검증
- [ ] PR·CI(ubuntu+windows)·머지
- 이연(M2, M5 가 한 것): 체결통보 리스너(`_notice_listeners`)
- 이연(M5):
  - M6: killswitch·hedge·`cash_park`·`excluded_tickers`(monitor 의 헤지/현금 운용 건너뛰기·`cancel_buys`)
  - M7: 공시/뉴스 트리거 생산(`news.post_ingest` 훅)·`analyze_ticker` kind(종목 락 `locks.lock_for` 공유)·discover·`reevaluate` 의 dry_run 담긴 요청 실행 단언·US 거래소 보강 단언
  - M8: memory(메모리 브레이커 인자·`test_resume` memory 1건)·`close` 무효화 종가표(`close_table` 콜러블 주입, Ruling 14)
  - M9: `ops_check` 잡·health
  - M10: fo 분기·`test_balance_dispatch` 3건·`test_fills_fo`
  - M11: jobs 라우터(웹 큐 API)·섹터 자동분류(`_classify`)·웹 시세 허브 `hub_blocked` 비켜서기(live_quotes yielding)
  - 커널 후보: `_close_stale_sessions` 공개 헬퍼화(spot jobs 와 runner 중복)

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
- [ ] 사용자 확인 대기: `channels.naver_blog` 기본값 — 옛 기본 `ranto28` 복원 여부 결정 (M3)
- [ ] 사용자 확인 대기: `EDGAR_USER_AGENT` 시크릿 입력 (M3 ingest, EDGAR 수집)
