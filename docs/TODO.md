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
- [x] PR·CI(ubuntu+windows)·머지 (#3, main 36b5c2a)
- 이연(M0 리뷰):
  - `contracts._default_roots` 동적 import 를 discover 경유로
  - `import_violations` 루트 추론을 명시 키워드로
  - bare `import trader.features.a` 가 서비스 기능 간에 허용되는 구멍 막기(+테스트)
  - `test_discover_real_package…` 실제 목록 단언
  - WEB_TOKEN 을 `secrets/.env` 에서도 읽기(python-dotenv)
- 이연(M2 리뷰, minor): `Boot.secrets` 타입 `Secrets | None`·`build_web_deps` 재읽기 제거; `settings.py` 자리표시자 판정 헬퍼화; `commands.py` echo 예외 집계(try/else)·미인식 명령 도움말 블록 중복; `notify.enabled=false` 와 토큰 없음 문구 구분; `modes.py` docstring; `telegram.py` `ctx: Any`; T1 `load_secrets` UnicodeDecodeError 바이트 위치 노출; T9 `accounts add` ValidationError 원문 echo 가독성; T10 `/mode … fast` 경로 단언 약함; T4 `/status` 절별 격리(M9 Task 1 완료)·`commands.registry` 검사를 `registry.contracts.check` 로 이동(M4)·데몬 telegram 잡 예외 내성(M4); 핸들러가 컨텍스트의 accounts/secrets 를 쓰도록
  - 상세 차이는 `features/accounts/README.md`·`docs/architecture.md` 「notify」 의 "알려진 차이"

## M3 — features/ingest + saveticker
수집(KIS·DART·EDGAR·뉴스·커뮤니티·거시)과 saveticker 수집기·리더·데몬 잡. 완료 기준: 옛 `sources/*`·`saveticker/*` 테스트 이식 통과.
- [x] Task 1: 커널 전제(의존성·paths·bundle·run_store·hooks·DaemonDeps·teardown·디스플레이 skip)
- [x] Task 2: saveticker 설정·경로·모델·스토어·보고서
- [x] Task 3: saveticker 파서·플랜·API 계약·네이티브 호스트
- [x] Task 4: saveticker Chromium·브라우저·감독·자동화 (2026-11 Playwright 수집기 제거 — Chrome 확장만 남김)
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
  - (M7 회수 완료): `_news_trigger`·`_discover_body`·`_disclosure_trigger`·`_events_check`·`ticker_names` 캐시·`peer_fn`·`universe check|sectors`, 옛 `test_daemon_news` 7건·`test_daemon_disclosure` 3건
  - M11: `/api/news`·`/api/control/manual-inputs`·`/api/system/saveticker`·restart → M11 에서 해소(`/news`·`/news/manual`·`/system/saveticker`·`POST /system/saveticker/restart`)
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
  - (M7 회수 완료; RL 은 M8, `AnalystRole` quant 복원은 Ruling M7-K1 로 하지 않음): quant·theme
  - M8: memory·calibration·`quick.py`(`ask_json`·`probe_usage`)·`test_quick` 9건·결과 dict `memory_entry_id`·`memory_entries` 단언
  - M10: fo 세션·`test_session_fo`·`test_schemas_fo`·`test_ticks_fo`·`test_usage_fo` → M10 에서 해소(fo `test_session`·`test_schemas`·`test_usage_fo`·`test_ticks_fo`)
  - M11: `decision_trace` → M11 에서 해소(`/decisions/spot/{id}/trace`)
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
- [x] PR·CI(ubuntu+windows)·머지 (#6, main a12d41f)
- 이연(M2, M5 가 한 것): 체결통보 리스너(`_notice_listeners`)
- 이연(M5):
  - M6: killswitch·hedge·`cash_park`·`excluded_tickers`(monitor 의 헤지/현금 운용 건너뛰기·`cancel_buys`)
  - (M7 회수 완료): 공시/뉴스 트리거 생산(`news.post_ingest` 훅)·`analyze_ticker` kind(종목 락 `locks.lock_for` 공유)·discover·`reevaluate` 의 dry_run 담긴 요청 실행 단언·US 거래소 보강 단언
  - M8: memory(메모리 브레이커 인자·`test_resume` memory 1건)·`close` 무효화 종가표(`close_table` 콜러블 주입, Ruling 14)
  - M12: `ops_check` 잡·health(`kernel/ops`, M9 플랜 A15 에서 M12 로 옮김)
  - M10: fo 분기·`test_balance_dispatch` 3건·`test_fills_fo` → M10 에서 해소(fo `fills.sync`·`balance` 분기, `tests/features/fo/test_fills.py`·`test_balance.py`)
  - M11: jobs 라우터(웹 큐 API)·웹 시세 허브 `hub_blocked` 비켜서기 → M11 에서 해소(`/jobs`·control `ws_session_busy:<env>`)
  - M12(M11 에서 옮김, 비웹): 섹터 자동분류(`_classify`)
  - 커널 후보: `_close_stale_sessions` 공개 헬퍼화(spot jobs 와 runner 중복)

## M6 — features/regime + 의도 생산자
국면·stance·시장 세션·킬스위치·블랙아웃, hedge·cash_park·rebalance, scan 의 테마·사건. 완료 기준: 옛 테스트 이식 통과.
- [x] 착수
- [x] Task 1 커널 승격 — 세션 공용 스키마·토론 요약·도구 헬퍼, 포트 Protocol, `latest_positions`
- [x] Task 2 커널 소수정·M5 이연 정리·플레이크 보강
- [x] Task 3 regime 뼈대 — 설정·원장 v10·국면·킬스위치·블랙아웃·메시지
- [x] Task 4 `ports["regime"]` + spot executor 게이트 배선·`excluded_tickers`
- [x] Task 5 시장 세션 — `market_view`·도구·`trader tool market`·에이전트 5종·fake
- [x] Task 6 `{market}_regime`·`ksd_events` 잡·큐 `market_view`·`trader regime`·`/kill`·상태 절
- [x] Task 7 spot 헤지(`hedge_tick`)·킬스위치 회수 배선(balance 경로)
- [x] Task 8 현금 운용(`cash_park`)
- [x] Task 9 주간 리밸런스(`{market}_rebalance`·큐 `rebalance`)
- [x] Task 10 `features/scan` 뼈대 + 테마(`theme.py`)·원장 v11 `theme_heat`
- [x] Task 11 사건 감지(`events.py`·`membership.py`)·`events.yaml`·`news.post_ingest` 훅
- [x] Task 12 `{market}_theme` 잡·`ports["scan"].theme_of`·`trader scan`·spot 테마 안전장치 배선
- [x] Task 13 문서·템플릿·체크리스트·골든 재검증
- [x] PR·CI(ubuntu+windows)·머지 (#7, main 10c3cbd)
- 이연(M6):
  - (M7 회수 완료; `events check` CLI 는 훅으로 대체): discovery 연동(scout 후보 검증·병합·`event_watch` 입력, 옛 `test_events` 3·4·9·10)·`theme.compute` 의 `vol_z`(피처 스토어)·리밸런스 `sector_of` 주입(섹터 상한)·screener regime 컨텍스트(옛 `scan/test_screener` 2건)
  - M8: `theme.mature` 데몬 호출(종가표 주입, 피처 스토어)·`metrics.decision_distribution`(헤지 결정 제외 단언은 `source` 로 대체)·`ml_trained` 메시지·`_default_sector_of` 예외 로그·`mature(close_table=None)` 테스트
  - (M9 회수 완료): `_scalp_open` → `ports["scalp"].open_tickers` 합집합 · `hedge_tick` pending 플래그 시 데몬 재시도 없음·리밸런스 잔고·시세 전부 실패에도 done 플래그·stale 예탁원 알림 = 옛 동작 보존 확인(변경 없음) · 커널 `status_message` 절별 격리 완료 · `market_view` QueueKind `trading=False` 유지(Ruling A15e, regime README 규칙 절) · `test_cli_regime` scalp 1건 → `tests/features/scalp/test_cli.py` · 큐 kind `rebalance` `RebalanceParams` 완료 · `timeout_by_kind` `market: 600` 기본값 완료(기존 settings.yaml 사용자는 템플릿 재복사 안내 유지)
  - M10: 커널 `latest_positions` 에 asset 필터 없음 — fo 행 생기면 시장 세션 입력에 파생 포지션 섞임; spot `_latest_positions` 와 통합 → M10 에서 해소(커널 `latest_positions(asset=)`)
  - M10(fo 이식 때): FAKE 헬퍼·검증/요약 헬퍼의 spot/regime 중복 → `kernel.session` 승격 → M10 에서 해소(`kernel/session/{fake,tooling}.py`, regime·fo 가 import)
  - M11: 웹 `/api/system/theme`(옛 `test_daemon_theme::test_api_theme`) → M11 에서 해소(`/news/themes`)
  - M12: `trader status` 의 국면 절(옛 `test_status_regime_section`)
  - 사용자 확인: `models.market_researcher_bull|bear` 모델 키가 옛 `researcher_bull|bear` 와 분리됨(옛 설정을 따르지 않음) — 필요하면 `config/settings.yaml` 에 키 추가

## M7 — features/ml + scan 확장 + spot scan_analyze
피처 스토어·학습·레지스트리·퀀트 리포트·유니버스 PIT(`features/ml`), 정량 필터·ML 랭커·스크리너·발굴·뉴스/공시 트리거·관심종목(`features/scan` 확장), `{market}_scan_analyze` 잡·`05_quant`(`features/spot`). 작업 큐는 M5 커널 그대로(`features/jobs` 패키지 없음). 플랜 `docs/superpowers/plans/2026-11-03-m7-scan-ml.md`. 완료 기준: 옛 테스트 이식 통과.
- [x] 착수(브랜치 `feat/m7-scan-ml`, 플랜 작성)
- [x] Task 1 의존성(sklearn·joblib·pyarrow·numpy) + 커널 전제 — `MlPort`·`ScanPort` 확장·`QuantReport`·원장 헬퍼/`horizon`/`names` 승격
- [x] Task 2 커널 소수정 — 큐 우선순위·내장 `/run`·ingest 훅 `deps`·`peer_fn` 포트 해석·`regime.api.excluded_tickers`
- [x] Task 3 ml 뼈대 — `ml.yaml` 섹션·원장 v12 `universe_members`·피처 스토어·데이터셋
- [x] Task 4 ml 모델 — `walkforward`·`QuantModel`·레지스트리
- [x] Task 5 ml 학습·튠·시점 유니버스·`05_quant` 리포트·`trader ml` CLI
- [x] Task 6 ml 잡(`ml_train`·`universe_members`)·큐 kind `features_*`/`ml_train`·`daemon.bootstrap` 훅·`ports["ml"]`
- [x] Task 7 scan 정량 필터·ML 랭커·스크린 백테스트·관심종목·`discovery` 설정·테마 `vol_z`
- [x] Task 8 scan 유니버스 점검·섹터 제안·`ports["scan"].sector_of`·`trader scan universe`
- [x] Task 9 scan 발굴 — 스키마·`discovery`·`tool scan` 도구 3종·scout/screener/scan-orchestrator 에이전트
- [x] Task 10 scan 스크리너·세션 kind `screen`/`discover`·FAKE·`run_scan`·`trader scan run`
- [x] Task 11 scan 트리거(뉴스/공시 재분석)·큐 kind `discover`·텔레그램 `/watch`
- [x] Task 12 spot `{market}_scan_analyze`·큐 kind `scan_analyze`/`analyze_ticker`·`05_quant`·`sector_of`·스케줄
- [x] Task 13 문서·템플릿·체크리스트·전체 재검증
- [x] PR·CI(ubuntu+windows)·머지 (#8, main d72d434)
- Ruling(M7): A1 랭커는 scan 소유(ml→scan 순환 방지, `ml.api` 소비) · K1 `AnalystRole` 은 4종 유지, `QuantReport.role="quant"` 별도 · S1 `regime` 스케줄을 `07:25`/`08:25 ET` 로 앞당겨 scan 앞에 둠 · `features/jobs` 는 커널 큐로 흡수(QueueKind 만 추가, `/run` 은 커널 내장) · `universe_pit` 는 ml 소유
- Ruling(M7, 실행 중 추가): L1 lightgbm 은 선택 의존이고 `import` 가 OSError(libomp 없음)여도 sklearn 으로 대체 · P1 `ROUND_TRIP_BP` 는 scan 백테스트에 복제(레인 `costs` 를 못 씀) · P2 플랜이 강제한 옛 결함(mkdir-on-read·promote 미강등·`max_event` 8/0·`schema_version` 1·`news_trigger_at` 선갱신)은 동작 보존 · T6-1 `peer_medians` 는 옛 `_peer_section` 모양 유지 · T9-1 발굴 세이브티커 폴더는 `saveticker_api.load_cfg().dir` · T10-1 옛 `scan_line` TypeError 는 고침 · T10-2 `run_scan` http 는 `ingest_api.owned_client(ctx.ext.get("http"), timeout=15)`
- 이연(M2, M7 가 한 것): watchlist·`/watch`(옛 `test_control` watchlist 2건)
- 이연(M7):
  - M8: RL 사이저 전부(`ml/rl/*`·`rl_shadow`·executor RL 훅·`trader rl`·`rl_train` kind·옛 테스트 8건·`test_ml_train_job_trains_sizer_even_when_not_enabled`; `ml.yaml` 의 `rl_sizer`·`calibration` 절은 설정만 둠)
  - M8: 섀도 포트폴리오 기록(`scan_analyze.post` 훅 자리, 옛 `test_scan_analyze_body_records_shadow_portfolios` 2건)·reflect 스냅샷 `snapshot_fn`(그 전까지 일일 스냅샷은 `features_snapshot` cron 06:30 KST)
  - M11: 웹 `/api/features`·`/api/ml` → M11 에서 해소(`/features/*`·`/ml/models*`); `RunJobButton` 은 M11b(프런트)
  - M12: `trader status` ML 절·ops.health `disclosure` 영역(`test_health_disclosure_area_critical_for_held`)
  - M12(M11 에서 옮김, 비웹): 섹터 상한은 yaml 에 없는 종목(유니버스 밖 발굴)에는 적용되지 않음 — `auto_classify` 호출자
  - M12(M11 에서 옮김): `scan_analyze._close_stale`·spot `_close_stale_sessions` 를 커널 공개 헬퍼로 통합
  - 알려진 차이(README 에 기록): `QuantModel` pickle 모듈 경로 변경으로 옛 `state/models/quant/*` 재학습 필요; `ROUND_TRIP_BP` 상수가 scan 백테스트에 중복(spot `costs` 는 레인 모듈)

## M8 — features/evaluation + briefing
메모리·보정·reflect·섀도·성과·readiness·backtest·paper_review·일일 리포트·RL 사이저(M7 이연), 아침 브리핑. 완료 기준: 옛 테스트 이식 통과. 플랜 `docs/superpowers/plans/2026-11-04-m8-evaluation-briefing.md`(브랜치 `feat/m8-evaluation-briefing`).
- [x] 플랜 작성(Task 1~14, Ruling A1~A15)
- [x] Task 1 커널 비용 상수 승격·`EvaluationPort`·`MlPort.close_table`/`extra_targets`·ml/scan/regime api 수출·MonitorContext 종가표 주입
- [x] Task 2 커널 `session/quick.py`(ask_json·probe_usage)·내장 잡 `usage_probe`·`ledger_backup`
- [x] Task 3 evaluation 뼈대·설정 절·원장 v13·스키마·메모리 store/vector
- [x] Task 4 메모리 만기 채점 outcomes·FIFO 로트 lots
- [x] Task 5 보정 calibration·backtest·evaluation 포트(메모리·보정·브레이커)
- [x] Task 6 섀도 포트폴리오·open_window·`scan_analyze.post` 훅
- [x] Task 7 성과 performance·metrics
- [x] Task 8 일일 리포트·paper_review·`/daily`·status 「성과」·잡
- [x] Task 9 readiness·CLI `trader evaluation readiness|backtest|paper-review|calibration build`
- [x] Task 10 복기 세션 kind `reflect`·에이전트·FAKE·`submit-reflection`·`reflect` 잡/큐·CLI·`reflect.mature` 훅 발화
- [x] Task 11 RL 사이저 `rl/`·`rl_train` 잡/큐·CLI `rl …`·포트 RL 메서드
- [x] Task 12 spot 통합(메모리/보정 컨텍스트·`35_calibration`·`memory_entry_id`·브레이커·RL)
- [x] Task 13 features/briefing(아침 브리핑, haiku 단발)
- [x] Task 14 문서·템플릿·TODO·전체 재검증
- [x] PR·CI(ubuntu+windows)·머지 (#9, main 766e131)
- Ruling(M8 플랜): A1 RL 사이저는 evaluation `rl/`(ml→evaluation 순환 방지; `rl_train` 독립 cron 토 10:30) · A2 레인 접점은 `ports["evaluation"]` 하나 · A3 `memory_entries` 에 `realized_return`·`alpha_vs_benchmark` 없음(스펙 §2.4) · A5 `round_trip_bp` 커널 승격(M7 P1 해소) · A6 `MonitorContext.close_table` 주입(M5 Ruling 14) · A7 `features_snapshot` 유지 + reflect 재스냅샷 · A8 retention prune 은 M12, fills 선동기화 없음 · A10 `usage_probe` 커널 내장 interval(1800 고정) · A13 CLI 는 `trader evaluation …` 만(최상위 별칭은 M12) · A15 `test_performance_fo` 는 M10 (→ M10 에서 해소)
- Ruling(M8, 실행 중 추가): T2-R1 `quick.ask_json` 은 `agents.with_schema` 재사용 · T9-R2(T9-R1 뒤집음) `readiness.interval_kinds_of` 는 interval 잡 `kind` 수집(커널 잡 큐·내장 잡 포함) · `store.held_at` 은 `account_nav` 만 있는 날을 보유 없음으로 · 집행 브레이커는 계좌 자신의 30일 NAV(paper 다수면 마지막) · `_rl_record` 는 exec-key 선점 뒤(선점 실패 시 `applied=0`)
- 이연(M8): M10 `test_performance_fo` 6건·fo `nav_series` stub · M11 `/api/performance`·`/api/rl`·`/api/memory`·`lots` CSV(→ M11 에서 해소: `/analytics/*`·`/accounts/{id}/tax-lots?format=csv`)·`metrics.pending_approvals/account_stance/stance_by_market`(→ M12, 웹은 계좌 행 `pending_approvals` 만) · M12 reflect 의 `retention.prune`·status/health 보정 재점검·최상위 CLI 별칭 결정·`usage_probe.interval_min` 반영 → fo `nav_series` 는 M10 에서 해소

## M9 — features/scalp
단타 Lane. 완료 기준: 옛 `scalp/*`·`test_daemon_scalp` 이식 통과. 플랜 `docs/superpowers/plans/2026-11-04-m9-scalp.md`(브랜치 `feat/m9-scalp`).
- [x] 플랜 작성(Task 1~8, Ruling A1~A16)
- [x] Task 1 커널·spot·scan 준비(`StockExecPort`·`ScalpPort`·`ScanPort.watchlist`·tradability 승격·`_kind` 접미사·`/status` 절 격리·A15 소수정)
- [x] Task 2 scalp 뼈대(feature·settings·schemas·messages·fake·README·템플릿·기능 목록 테스트)
- [x] Task 3 signals(진입 신호·청산 규칙·백테스트)
- [x] Task 4 state·judge·`scalp-judge` 에이전트
- [x] Task 5 engine(허용·후보·진입·판정 회수·틱·digest·report)
- [x] Task 6 exits(다리·체결 동기화·취소·흡수·이월)
- [x] Task 7 jobs·ports(`{market}_scalp` 잡·outbox flush·teardown·`ScalpPort`·리밸런스 제외)
- [x] Task 8 CLI·status·README·CLAUDE.md·TODO·전체 재검증
- [x] PR·CI(ubuntu+windows)·머지 (PR #10, main b1de65f)
- Ruling(M9 플랜): A1 주문 경로는 spot 이 노출하는 `stock_exec` 포트(execute·cancel·sync_fills; 게이트 복제 기각) · A2 `scalp` origin 은 spot ORIGINS 유지 · A3 JSON 상태 파일 유지·마이그레이션 없음(legacy 테스트 1건 미이식) · A4 잡 `{market}_scalp` kind `scalp` interval `{market}.monitor.interval_s`·게이트 `none`(본문이 창·휴장·일시정지 거르고 outbox flush) · A5 판정자 `quick.ask_json` + `session_allowance(kind="scalp_judge")` · A6 후보 = `_scan` 체크포인트 + `job_requests(source=news)` + `ScanPort.watchlist` · A8 모의 전용 `skip_accounts`, `block_reason` 커널 승격 · A9 `ScalpPort.open_tickers` 로 리밸런스 제외 · A10 `SCALP_EXIT` 에 `carry_over` 추가 · A12 `event.at` 은 주입 시계 · A13 커널 `status_message` 절 격리 · A14 `_kind` 가 `_entry/_exit` 꼬리 제거 · A15 `OPEN_STATUSES` 커널, `DEAD_STATUSES` import, `timeout_by_kind` 기본 `market: 600`, `RebalanceParams`, `market_view trading=False` 유지, `ops_check`/health 는 M12 · A16 판정 컨텍스트 뉴스는 빈 목록(`InputsPort.recent` 는 M11/M12 후보)
- Ruling(M9, 실행 중 추가): P1 `timeout_by_kind` 기본값 테스트는 `tests/kernel/config/test_llm_settings.py` · T4-R1 `judge_context.regime` 은 `ports["regime"].load` 저장 레코드를 그대로(없음 → `{state: None, count: None}`; 플랜 A5/A6 의 `state()/core_count()` 문구 대체) · T5-R1 `max_positions` 는 `allowed()` 가 아니라 진입 루프가 open+pending 으로 센다(옛 동작, 플랜 A7 정정) · T6-R1 커널 `core.cancel` 은 잔량(`qty − SUM(fills.qty)`)으로 취소, 잔량 0 이면 브로커 호출 없이 `ok False` "남은 수량 없음(전량 체결)"(spot US 취소에도 적용, KR 은 `QTY_ALL_ORD_YN=Y` 라 무영향) · T7-R1 `SCALP_TICK.gate="none"`, 본문이 `ctx.gates["monitor"]` 를 런타임에 재사용(창 밖·주말·휴장)·`accounts.paused` 직접 확인·건너뛴 모든 경로에서 outbox flush, `exchanges={}` · T8-R1 scalp CLI --market 이 KR/US 외면 exit 1(옛은 US 처리) · T8-R2 report --live US 거래소 = 판정 사건 → 원장 ticker_exchanges → NAS(옛 universe.us, 레인은 scan import 불가) · F2 scalp backtest 도 today=run_date_for 를 넘김(옛 UTC 날짜 결함 수정)
- 이연(M9):
  - M10 에서 해소: FAKE 헬퍼 `kernel.session` 승격 · 커널 `latest_positions` asset 필터
  - M10 Task 1 에서 처리: F1 `skip_accounts` 의 `policy != "auto"` 추가는 **검토 완료 — 보존**(approve 모의 계좌는 사용자가 의도한 승인 모드) · F3 adopt SQL `origin='scalp'` 필터 적용 · F4 `scalp_body` monitor 게이트 부재 시 fail-closed 적용 · `judge_for` 단일체 생성 `threading.Lock` 적용(커밋 c9539d4)
  - 이연 유지: 틱 단위 브로커·시세 클라이언트 캐시(`ScalpContext`) → M12 · spot monitor 가 단타 보유분(`open_tickers`)을 손절·트리거 대상에서 빼지 않음 → M12 검토(M11 은 웹만) · 다음 플랜 Global Constraints 의 "이름 금지" 문구를 "계좌·개인·수집 원문 금지" 로 좁히기(상장사 이름 리터럴은 저장소 관례)
  - M11: 웹 `/system/scalp` 자원·SSE `scalp` 키 → M11 에서 해소
  - M12(M11 에서 옮김, 비웹): `InputsPort.recent`(판정 컨텍스트 뉴스, 지금 `news=[]`)
  - M12: `ops_check`/health · `trader status` 단타 절 CLI 점검
  - 테스트 보강: US 시장 테스트(`exchange_of` 폴백·`analyze_targets` 거래소) · `StockExecPortImpl.cancel` broker None 가드 · 커널 tradability 테스트의 spot 헬퍼 import 정리 · `test_state` 종목명 리터럴 합성화

## M10 — features/fo
파생 Lane(`LegStore`, 자체 실행기 — A1). 완료 기준: 옛 `fo/*` 계열 테스트 이식 통과. 플랜 `docs/superpowers/plans/2026-11-04-m10-fo.md`(브랜치 `feat/m10-fo`).
- [x] 플랜 작성(Task 1~9, Ruling A1~A22; 4683902)
- [x] Task 1 커널·accounts·scalp 준비(`fo_accounts/fo_brokers`·파생 장 시간·`rule_hit`/FAKE 승격·`latest_positions(asset=)`; bdcd216, M9 F3/F4 c9539d4)
- [x] Task 2 fo 뼈대(feature·settings·마이그레이션 14·schemas·types·pricing·messages·템플릿; bcb5a8f)
- [x] Task 3 store·`LegStore`·상태기계 복구/adopt(19b256d)
- [x] Task 4 게이트·사이징·트리거 순수 모듈(e9d9ea4)
- [x] Task 5 다리 실행기·체결 동기화·잔고 대조(9077ff6·e5c6f5a, fix 32f151f·6d6d65a)
- [x] Task 6 입력·아침 세션·LLM 도구·세션 종류·FAKE·에이전트(dfcb7ff, 마이그레이션 테스트 fc55bd1, 승격 fix db6f27f)
- [x] Task 7 모니터 엔진·재평가·데몬 잡·큐 kind(3061f2e)
- [x] Task 8 CLI·order-test·`/fo`·상태 절(a56b2db `fo_reconcile` 큐 kind·f48a7b6)
- [x] Task 9 README·architecture·CLAUDE.md·TODO·전체 재검증(f7d4840)
- [x] 최종 리뷰 fix(세션 상한 `skipped`·실전 파생 2중 방어·Review Focus 테스트 2건·`store.tx` 롤백 가드·상태 절 생략·README 정정; 0a066c8)
- [x] 옛 tests/broker/test_fo_market.py::test_option_quote_iv_is_percent_and_reprice_converts 이식(`tests/features/fo/test_pricing.py`, A21)
- [x] PR·CI(ubuntu+windows)·머지 (#12, main 8016edd)
- Ruling(M10 플랜): A1 fo 는 자체 실행기(커널 `transmit` 불사용, 한 트랜잭션·`FoOrderRequest`·fo 알림) · A2 `AccountsPort.fo_accounts/fo_brokers` · A3 `LegStore(IdemStore)` · A4 `fo_flags` 테이블 대신 control 플래그 7 접두 · A5 잡 3개(`gate="none"`, 본문이 파생 시계)·큐 kind 4개 · A6 세션 `fo`/`fo_resume`·`extra_cap_key=None`(fo 쪽 상한) · A7 도구 `fo`·`submit-fo-decision` · A8 판정 입력의 뉴스·거시는 M11 `InputsPort.recent`(`news_macro` 절 빈 채) · A9 커널 승격(`rule_hit`·FAKE·`tooling`·`indicators`·`get_trigger`; 사본 허용은 리뷰 후 철회) · A10 타 기능 설정·테이블은 속성 접근/raw SQL · A11 CLI 9개 · A12 `/fo`·상태 절(health `_fo` M12·웹 M11) · A13 메시지 · A14 `order_test` · A15 M9 F1 보존·F3/F4/Lock 적용 · A16 템플릿 · A17 테스트 `tests/features/fo/` · A18 주입 시계 · A19 `FoProduct` · A20 `in_entry_window`(끝 15:30 포함) · A21 IV 테스트 · A22 `nav_series(asset="fo")`
- Ruling(M10, 실행 중 추가): `store.tx` 는 자기 중첩만 합류·암묵 tx 는 선commit · 커널 `insert_fill(commit=)` 추가 · `positions.recover` 진입부 commit 가드 · fo 상한은 원자적 예약 없음 · paused 는 halted 플래그·정산 전이도 차단(옛은 dry_run 만) · `reevaluate_request` 는 사람 요청도 게이트 · 엔진 재평가 enqueue source `fo_monitor` · `fo_session` 큐는 파생 시계 불사용 · 마스터는 CLI·데몬에서 온라인 적재(도구만 offline) · `fo_reconcile` 큐 kind 4번째 추가 · `brokers()` 반환형 list · 실전 파생 skip 결과에 `status/record_only/mode` 키
- 이연(M10):
  - M11: 웹 `/api/fo/*`·SSE `fo` 키 → M11 에서 해소(`/fo/*`·`/analytics/fo/*`·`/system/fo`)
  - M12(M11 에서 옮김, 비웹): `InputsPort.recent`(fo 판정 뉴스·거시, 엔진 `_novel_news` 의 `news_items` raw SQL 도 포트로) · spot monitor 단타 보유분 검토 · FAKE 사본 잔여(regime `_turn` ≈ `fake_turn`) · `AccountsPort` dict 가짜 포트 정리(spot/scalp/daemon 테스트의 `brokers` dict 반환) · `views.account_summary` 의존 `test_performance_fo` 2건 → 해소(M12 Task 14·17; 단타 보유분·`test_performance_fo` 는 「M12 이후」)
  - M12(M11 에서 옮김) 커널 승격 후보: `reeval.handled_marker`·`_wrap_section`·`_boot_notifier`·`_check_hm` → 해소(M12 Task 14; `_wrap_section` 은 「M12 이후」 사본 허용)
  - M12: health `_fo` 4항(unknown·exit_failed critical, 정산 대기 초과, 옵션 매도 D-1 미청산, 15:45 뒤 살아 있는 파생 주문; M12 Task 4) · `kis ping` fo 잔고 줄(M12 Task 18 — `accounts ping`) · 틱 단위 브로커·시세 클라이언트 캐시(fo 모니터 포함 — 매 틱 `fo_brokers`·`market_client`×3·`fo_master.load` 재조립, 키 없는 fo 계좌면 매분 경고 로그; 「M12 이후」) · `.claude/settings.json` deny 목록(옛 `test_settings_denies_fo_session`; M12 Task 19 `tests/test_settings_json.py`)
  - 보강(minor): 비공개 `jobs._fo_ctx` 를 공개 이름으로(cli 가 SimpleNamespace 로 호출) · `adopt` 에 `offline=True` 고려(네트워크 다운 시 30s 대기) · `close --confirm` 성공 경로 테스트 · `order-test`/`monitor` CLI 테스트가 배선만 확인 · `off`/`approve` 모드 정산 전이 테스트 · `test_ticks_fo` 가 `test_ticks` 중복 · spot `test_monitor_rules` 의 `rule_hit` 단언 중복 · `ports.py` `fo_accounts/fo_brokers` docstring 동일 · 커널 `monitor_rules` docstring 이 기능 이름 언급 · fo settings `_check_hm` 로컬(accounts 와 같은 정규식이면 커널 승격) · `FoExecContext.settings/accounts` Any · `positions.recover`/`store.tx` docstring 정합·합류+예외 테스트 · `LegStore.claim_reserved` raw SQL·recheck 예외 시 `sending` 잔류 · 실전 분기가 accounts 포트 없으면 KeyError(옛은 skip) · `reeval_apply` 훅 없을 때 지연 import · T6 `_support._tool_ctx` 가 dataclass 에 `_inputs` 부착 · T6 `tools.py` `_schema_error` 인라인 중복(커널 `tooling.schema_error` 로 교체 후보) · T6 `build_tool_app` 경유 CLI 경로 테스트 부재 · 큐 `fo_session`(사람 요청)은 paused 를 검사하지 않아 일시정지 중에도 LLM 세션 비용 발생(주문은 기록 전용 — Ruling 으로 이연) · `fills._replace_fill` 의 `fills.id` 재생성
  - 실기 2(보유 행이 있어야 확인): `features/fo/README.md` 「실기 미확인」

## M11 — features/quotes + 웹 API 12 자원 (백엔드만)
웹 API 12 자원·SSE 키 생성·계약 테스트. 완료 기준: 모든 자원 엔드포인트 테스트 통과, 옛 엔드포인트 대응표 100%. (M11b: 새 웹 UI 구현은 아래 「M11b — 웹 UI 프런트(web/)」 절, 플랜 `docs/superpowers/plans/2026-11-05-m11b-web-ui.md`) 플랜 `docs/superpowers/plans/2026-11-04-m11-web-api.md`(브랜치 `feat/m11-web-api`), UI 설계 `docs/superpowers/specs/2026-11-04-m11-web-ui-design.md`.
- [x] UI 설계·플랜(a01cec9·1ce99c1, 보강 71f08db·78f2fef·3ba6003·183a9a0, 사전 점검 R4 068ee3d)
- [x] Task 1 웹 셸 tailnet 게이트·`X-Server-Time`·merge·nav 생성(4b967bf)
- [x] Task 2 마이그레이션 15 `notices`·`control_log` + 기록 경로(f649c36, fix 8bacd60)
- [x] Task 3 커널 자원 system·jobs·runs·calls(b72002e)
- [x] Task 4 텔레그램 인라인 버튼·callback_query 라우팅(da5c492, fix 28f98f1)
- [x] Task 5 approvals 자원·decide·`approve_execute` 큐·텔레그램 버튼(daf713d)
- [x] Task 6 accounts·overview·settings 자원(40c9b98, fix c2e576f)
- [x] Task 7 positions 부분 응답·손절/목표·risk/overview(2ba5e60)
- [x] Task 8 decisions·orders 자원·상세·trace·CSV(708a14b·a0faed7, fix ac50206)
- [x] Task 9 `features/quotes` 실시간 시세 허브·`/quotes/fx`(bec2460)
- [x] Task 10 커널 SSE probe·usage-throttle + names·charts·features·news·settings 자원(d13cf8f·6ba3457, fix 11eb3d9)
- [x] Task 11 analytics·system 기능 절 자원(ff13701·4368f81·620c20c, fix 2ea864f)
- [x] Task 12 대응표 100% 검사(`tests/kernel/web/test_endpoint_map.py`)·프록시 헤더 루프백 차단·웹 포트 필터·trading kind confirm·문서(8dfdda8, 문서 커밋은 이 줄을 담은 커밋)
- [x] 최종 리뷰·fix wave(I1 조작 이력 `job:`·`account:`·`secrets:env`·텔레그램 `/run` · I2 웹 accounts 포트 `ReadOnlyAccounts` · I3 환율 음성 캐시·stale · M1–M7·M9(텔레그램)·M10 — 이 줄을 담은 커밋)
- [x] PR #13·CI(ubuntu+windows)·squash 머지(main ba9450f, 2026-11-05)
- Ruling(M11 사전 점검): R1 `POST /system/usage/ensure-fresh` 는 같은 출처만(토큰 없음, 큐에 probe 만) · R2 Task 6 이 `features/quotes` 최소 골격 선행 · R3 「오늘」 하위 메뉴 승인·현물·단타·파생 · R4 `/quotes/us` 없음(`/quotes?market=US`) · R5 커널 SSE probe 레지스트리 `KERNEL_PROBES`
- Ruling(M11 실행 중): T2-R1 마이그레이션 15 는 커널 `web` 소유 · T2-R2 값이 같으면 `control_log` 행 없음 · T2-R3 outbox 재전송 성공은 새 notices 행 · T2-R4 텔레그램 변경은 channel `telegram` · T3-R1 `WebDeps.ports`, 모르는 계좌는 실전 취급 · T3-R2 `usage_probe` 큐 kind + kind 목록 커널 단일화 · T3-R3 웹 포트에서 집행 포트 제외 · T3-R4 계좌 없는 trading kind 는 실전 계좌 confirm · T4-R1 callback_data 64바이트 초과는 ValueError · T5-R1(텔레그램은 fix wave, 웹 거부 알림은 M12)/R2(M12) 이연 · T6-R1 웹도 LIVE 확인을 줄 수 있음(사용자 결정 2026-11-05, 본문 confirm=계좌 id; 후속 커밋) · T6-R2 통합 현황은 `account_nav` 만(파생 NAV 는 `/fo/summary`) · T7-R1 단타 행은 현물 행과 별도 · T7-R2 `settings.scalp.accounts` 없음 · T8-R1 단타 결정 행 합성(id `scalp-<order_id>`) · T8-R2 `discovery_line` 없음 · T9-R1 quotes 는 시크릿으로 자체 시세 클라이언트 · T9-R2/R5 환율 KIS `FHKST03030100`(X/`FX@KRW`, 계좌 불필요) → `HHDFS76200200.t_rate` → FRED 하루 캐시 → 404(조사 `docs/research/2026-11-05-kis-fx-rate-api.md`, 후속 커밋) · T9-R3/R4 `ws_session_busy:<env>` 세션 양보(90초 자기 만료) · T10-R1 `/news/events` 는 공시만 · T10-R2 웹 `/names` 는 `ticker_names` 에 쓰지 않음 · T10-R3 `CONTROL_CURSOR_LIKE` 의 기능 접두 · T11-R1 `/system/fo` 는 파생 계좌 전체 · T11-R2 saveticker 재시작 = control 플래그 + 데몬 소비 · T11-R4 nav id `performance-overview` · T11-R5 비교 행 (계좌, 시장) · T11-R6 memory `q` = 종목 · T12-R1 자원 별칭표(첫 세그먼트 → 자원)
- 해소된 이연(M0 리뷰·M2): Host userinfo 거부 · `/api` catch-all 전 메서드 + 베어 `/api` · `dist` 존재 지연 검사 · 비루프백 바인드 경고·`extra_hosts`(`tailscale_hosts`) 연결 · `/api/accounts`·연결 키 라우트(`/settings/secrets`)
- 최종 fix wave 에서 해소: T5-R1 텔레그램 거부 「[거부]」 알림 · T6 `POST/DELETE /accounts`·`POST /settings/secrets` control_log · T7 fo `date` 파싱 500 · T8 조각 `date` 400(merge 가 HTTPException 재발생)·CSV `X-Partial` · T9 환율 음성 캐시·stale·`build_market` 예외 폴백·허브 `_drop(backoff=True)`·health `ws_session_busy` until · T11 성과 캐시 상한(모듈 LRU 64) · 프록시 헤더 존재만으로 차단 · `account_env` 파생 계좌 · 재리뷰 F7 `fx_cache:%` 커서류 등록·F8 quotes 문구(후속 커밋)
- 이연(최종 리뷰 뒤 남은 것 — M12 이연은 아래 「이연(M12)」):
  - T1: `app.py` 의 `auth._host_of` 비공개 import · nav 화면 KeyError 를 계약 검사로 · `build_web_deps` 3필드 테스트
  - T2: 상태 변경과 `log_change` 의 분리 커밋 · notices 는 데몬 notifier 만 기록(CLI·fo 도구 경로·NullNotifier 미기록)
  - T3: `read_status` 입력 두 번 조립 · `_enabled` 의 AttributeError 삼킴 · `interval_s` 는 schedule.yaml 이 아니라 JobSpec · `caps_by_kind` getattr 500 가능 · ensure-fresh 가 enqueue 전에 60초 도장
  - T5: 선점이 TTL 까지 남음(approve_execute 실패·만료 시) · 해제 감사 기록 없음 · decide 의 `_take_pending` 재구현 · spot routes 가 커널 `routers.jobs` 내부(`daemon_alive`·`DAEMON_DOWN`) import · 모의 승인 `ask` 의 "실전" 문구 · CLI `approvals approve` 가 decide 우회
  - T6: overview `day_pnl` 부분 합 표시 · `_positions_fp` 사흘 행 스캔
  - T7: 단타 행 `entry_price` 없으면 조각 전체 partial · fo "만기 D-n" 음수 · positions_view 의 `spot.executor.sector_of_for` 대신 `ports["scan"]`
  - T8: CSV 중첩값 repr · `NEWS_REF` fullmatch · 결정 N+1 · `decisions_view.py` 분할
  - T9: `ws_session_busy` 네임스페이스 선언 · fo 시장 시간 09:00~15:30 vs 파생 08:45~15:45
  - T10: scan `_ids` 유니코드 숫자 500 · charts `json_valid` 가드·레인 필터 · `re.match`+`$` → fullmatch · `market` 파라미터 관례·`_market` 헬퍼 중복 · `_TICKER_RE` 3벌·비공개 import · `ManualBody.text` max_length · `CONTROL_CURSOR_LIKE` 를 `Feature` 선언으로(T10-R3) · ml `active` 의 시장 선호
  - T11: 성과 캐시를 모듈 전역 → deps 단위로(상한은 fix wave 에서 LRU 64) · fo `_flags` LIKE `_`·int 변환 500 · memory 응답 모양 차이·자기 자신 최상위 · analytics 422/400 혼재 · 재시작 응답에 「수집 꺼짐」 안내
  - T12: 계좌 없는 trading kind(`discover`·`fo_session`)는 실전 현물 계좌가 있으면 모의 파생 세션도 실전 confirm 을 요구한다(fail-safe 방향) — 대상 레인별로 좁힐지 결정
  - 웹 테스트 공백(경로 탈출·same-origin 포트·SSE `_SHARED`) · `gen-sse-keys`/`gen-nav` 대상 경로·`web_dist()` 를 소스 트리 기준으로(지금은 `project_root()` — scratch `TRADER_HOME` 에서 `--check` 하려면 생성 파일 복사 필요) · `_hostname`/`_host_of` 정리
- 이연(M11b, 프런트): 새 UI 전체(설계 §9) · `RunJobButton` · 단타 행은 「단타」 절에 따로 · 결정 화면은 SSE `decisions`+`fo`+`orders` 구독 · 단타 결정 행은 상세 링크 없음 · `/settings/secrets` 는 토큰을 붙여 부름 · `discovery_line` 은 `/news/themes` 로 조립 · 파생 보유 부가 필드 필요 여부 · 수동 작업 confirm 입력은 실전 계좌 드롭다운(T3-R4/T12-R2 완화)
- 이연(M12): `kernel/ops` 판정으로 `/system/health` 교체(M12 Task 2) · 운영 이상 텔레그램 발송·쿨다운(M12 Task 1·2 `escalate` 상태 dedup, Task 8 watchdog 1h 재알림) · `tailscale_hosts/users/public_base` 실제 값 설정(「사용자 확인 대기」) · 텔레그램 버튼 실기 확인(「사용자 확인 대기」) · `ensure_web_token` 연결(M12 Task 11 — `trader app` 첫 실행만, `trader web` 은 부르지 않음) · KIS 환율 실기 확인(`FX@KRW` vs `FX@KRWKFTC` 값·자릿수·갱신 주기·빈 응답; `quotes.fx_code` 로 전환 — 「M12 이후」) · 거시 관측 저장소(`/news/events` 거시 — 「M12 이후」) · `settings.scalp.accounts` 계좌 키(「M12 이후」) · M12 절 「M11 최종 리뷰에서 옮긴 이연」 · 아래 「M11 에서 옮긴 비웹 이연」

## M11b — 웹 UI 프런트(web/)
M11 설계(`docs/superpowers/specs/2026-11-04-m11-web-ui-design.md`)의 새 웹 UI 를 `web/`(React 19 + Vite 7 + TS, vitest/jsdom)로 구현. 백엔드는 손대지 않는다. 플랜 `docs/superpowers/plans/2026-11-05-m11b-web-ui.md`(브랜치 `feat/m11b-web-ui`), 승인 목업 사본(17860b5). 완료 기준: 다섯 화면 + 결정 상세, `npm run typecheck`·`npm test`·`npm run build`·`gen-nav`/`gen-sse-keys --check` 통과.
- [x] Task 1 디자인 토큰·테마·테스트 기반(c3e306a, 보완 44cd5d5, fix 29a4526)
- [x] Task 2 api·clock·format·glossary·usage·health 순수 모듈(6807aa0, fix 6d5b285)
- [x] Task 3 SSE 리더 탭·시세 폴링·자원 훅(cf07c40, 플랜 2896b74, fix f8c5458)
- [x] Task 4 앱 셸 — 라우터·상태 띠·2단 메뉴·PWA 매니페스트(8a437e8, fix 4910f56)
- [x] Task 5 공통 부품 — 표/행 카드·확인 대화상자·잡 실행 버튼·차트(ca61703, fix c2a7ccc)
- [x] Task 6 오늘 화면 — 대기 승인·레인 카드·결정 목록(37aa5d4, fix ac0d33d)
- [x] Task 7 오늘 화면 — 주문·체결 타임라인·알림 피드·다음 할 일(555fe16)
- [x] Task 8 결정 상세 — 라운드·trace·결과(21d3bb9, fix 6196774)
- [x] Task 9 계좌·보유 통합 — 합계·환율·계좌 카드·전역 일시정지(017b82e)
- [x] Task 10 계좌별 — 보유·손절/목표·리스크·모드 전환·NAV 차트·실시간 평가(f4d39b5, fix 97e5698)
- [x] Task 11 성과 화면(af80166, fix cbd99bf)
- [x] Task 12 데이터 화면 — 뉴스·이벤트·테마·지표/모델·감시목록·차트(b3eac7c)
- [x] Task 13 시스템 화면(6e2b7ee, fix db54a33)
- [x] Task 14 반응형 점검(`responsive.test.tsx`)·다듬기(맨 폼 컨트롤 토큰화·한국어 줄바꿈·<1024 상태 띠 2줄)·문서(이 줄을 담은 커밋)
- 이연(프런트): 계좌 단위 일시정지 API 없음(`PATCH /accounts/{id} {paused}` → 화면 생략, 전역 일시정지만) · 옛 `/fo/positions` 부가 필드(`current`·`eval`·`budget_*`·`live_legs`) 없음 → 파생 행은 포인트·원화 평가만 · 단타 결정 상세 없음(`scalp-<id>` 422) → 단타 상세 링크 없음 · 웹 거부 알림은 M12(T5-R1/F1 — M12 Task 16 해소) · 아이콘 PNG(PWA 매니페스트 아이콘 래스터) 미작성(M12 Task 15 해소)
- 백엔드 요청(M12 후보, 이 마일스톤은 손대지 않음 — 전부 M12 에서 해소): SSE ping 이벤트화(지금 `: ping {...}` 주석이라 EventSource 에 닿지 않음 — `event: ping` 으로 바꾸면 `lib/sse.ts` 가 그대로 받음, T3-R1; M12 Task 15) · `news_collect` 큐 kind 없음(데몬 interval 잡뿐 → 뉴스 「지금 수집」 버튼 생략, T12-R1; M12 Task 15) · `features` SSE 키 없음(지표·모델은 `market_views` 구독이라 피처 갱신이 즉시 반영 안 됨, T12-R2; M12 Task 15) · `/positions/risk` 한도 필드 없음(리스크 카드는 한도 대비 대신 위험 기여 상위 3 막대, T10-R4; M12 Task 16 — 한도 막대와 기여 상위 3 병행, T16-R2)

## M12 — kernel/ops + 번들 + 전환
health·watchdog·status·retention, CLI 전체 점검, 번들(`packaging/build.py`·`build.yml`), `CLAUDE.md` 최종 다이어트, 전환 리허설. 완료 기준: `FAKE_CLAUDE=1 trader daemon --dry-run` 하루 무사고 → 사용자가 `install` 교체. 플랜 `docs/superpowers/plans/2026-11-05-m12-ops.md`(c52e51d, 브랜치 `feat/m12-ops`), 런북 `docs/runbook-transition.md`.
- [x] Task 1 `kernel/ops` health 러너·커널 영역 jobs/llm/orders·격상(718f4a2)
- [x] Task 2 `ops_check` 내장 잡·`/system/health` 를 `kernel/ops` 판정으로 교체(dd6cd70, fix cc64884)
- [x] Task 3 regime·ingest·evaluation·quotes·saveticker health 영역(42f7983)
- [x] Task 4 spot·fo health 영역(ddab7e9, fix e723d40)
- [x] Task 5 retention 내장 cron 잡·`ops.retention_days`(24f8020)
- [x] Task 6 status 렌더러·`trader status`·텔레그램 `/status` 커널 절(c8cf0cc)
- [x] Task 7 spot·scan·ml·regime·evaluation·accounts 상태 절 보강(1d334a9, fix def66e2)
- [x] Task 8 watchdog 외부 감시·`trader watchdog`(26505b3, fix 743f236)
- [x] Task 9 installer·bundle argv/icons/first_run_init·notify_os·`trader install`(c21db3a)
- [x] Task 10 메뉴바/트레이 앱 이식(9ea8c3e)
- [x] Task 11 `trader app`(데몬+웹+트레이)·`__main__` 더블클릭/네이티브 우회·`--version`·`ensure_web_token`(214be90, fix 88fe871)
- [x] Task 12 PyInstaller 번들·`build.yml`·아이콘·`docs/bundle-usage.md`(e39114a)
- [x] Task 13 selfcheck 무거운 스택·드라이버 점검·DB 미생성, CLI 경량 기동(db8c74f)
- [x] Task 14 커널 승격(close_stale·handled_marker·boot_notifier·check_hm)·Lock clock·hb.stop·`interval_from`(3f40a85, fix da612d7)
- [x] Task 15 SSE ping 이벤트·`news_collect` 큐 kind·`features` SSE 키·PWA 아이콘 PNG(99a7bcc)
- [x] Task 16 승인 취소 선점 해제·웹 거부 알림 큐·승인 통화·리스크 한도 필드(9d53837, fix 6bee4fc)
- [x] Task 17 `AccountsPort.brokers` 리스트 통일·`InputsPort.recent`·`ScanPort.classify`(0c9a327, fix d62e213)
- [x] Task 18 `names_fill` 잡·`instrument_names`·`accounts order-test`·`ping` fo 잔고·잔여 단위 테스트(cf4ef3b, fix 304d93e)
- [x] Task 19 CLAUDE.md 명령표·architecture `ops`·TODO·`docs/runbook-transition.md`·`.claude/settings.json`·세션 훅 `tools/session_start.py`(이 줄을 담은 커밋)
- [x] 최종 리뷰·fix wave(dccfea2 코드·0768168 설정·훅·이 줄을 담은 문서 커밋)
- [ ] PR·CI(ubuntu+windows)·머지
- [ ] 전환 리허설 하루 무사고(`docs/runbook-transition.md` 2절) → 사용자가 옛 `install --uninstall` 뒤 새 `install`(+`--watchdog`) (사용자)
- Ruling(M12 실행 중): PF-1 T2 가 웹 busy 사유 테스트를 지우고 T3 quotes 영역이 되살림 · PF-2 `uvicorn`·`create_app` 지연 import 는 T11, 기능 `cli.py` 경량화는 T13 · T1-R1 `to_web` 머리 단계 critical→fail·warning→warn·그 외 ok(watch 는 info 사유만) · T1-R2 항목 link 는 새 라우트(`/`·`/accounts/{id}`·`/system`·`/data`) · T2-R1 `schedule_jobs` 는 모든 게이트를 통과시키는 탐침 ctx 로 펼침 · T2-R2 `kernel/ops/__init__` 은 PEP 562 지연 재노출(순환 회피) · T2-R3 `/system/health` 캐시 경로도 데몬 생존·하트비트를 먼저 보고 죽었으면 직접 판정 · T4-R1 `holiday_fn` None 폴백 `is_us_holiday` 는 US 전용 · T7-R1 spot 최근 결정은 원장 `decisions` 테이블(최근 3건) · T7-R2 ml 절은 메타 `metrics.accuracy` · T7-R3 확인 없는 실전 auto 는 `auto→off(LIVE 미확인)` · T7-R4 텔레그램 `/status` 는 비어 있지 않은 절마다 `[제목]` 줄 · T11-R1 `trader app` 은 `Daemon.start(install_signals=False)`(잡은 스케줄러 스레드) + `WebThread` · T12-R1 PyInstaller 수집·npm 산출물 ⚠️ 는 v0.2.0 태그 CI 가 첫 검증 · T13-R1 selfcheck 는 DB 파일 존재를 먼저 보고 없으면 연결 안 함(boot 불변) · T13-R2 P19 축소 — `trader --help` 회귀 가드는 uvicorn·sklearn 부재만, fastapi·pandas 는 「M12 이후」 · T14-R1 regime/jobs 에는 boot_notifier 사본이 없음(spot·fo 둘만 통합) · T14-R2 기능 측 `close_stale` 로그는 중립 문구·info · T15-R1 테스트는 기존 `tests/features/ingest/test_job.py` 에 · T15-R2 `NewsCollectParams.market` 은 `Market` Literal 로(최종 fix wave) · T16-R1 리스크 한도 테스트는 기존 `test_positions_view.py` 에 · T16-R2 RiskCard 는 한도 막대와 위험 기여 상위 3 병행 · T17-R1 `spot/cash_park.py` 옛 형식 분기는 brokers 와 무관해 제외 · T17-R2 `InputsPort.recent(novel_only=)`·행에 `tickers`·`novel` · T17-R3 `untagged_only=` 키워드, fo `news_macro` 채움 승인 · T18-R1 현물 `accounts order-test` 는 신규 작성(옛 저장소에 없음) · T18-R2 statusline 파일만 최근인 경우의 `usage_probe` 건너뜀 테스트 · T18-R3 `instrument_names` 는 설정이 관측을 덮어씀(broker 는 DO NOTHING) · T18-R4 `test_order_paths.ALLOWED` 에 `accounts/order_test.py` 예외 수용(CLAUDE.md 불변식 예외 둘)
- Ruling(M12 T19): T19-R1 P12 가 T11 브리프보다 우선 — `trader web` 도 `ensure_web_token()`(최종 fix wave 에서 구현) · T19-R2 settings.json deny 에 브리프 밖 `accounts ping*`·`saveticker monitor*`·`uv run python -m trader …*` 3형 수용 · T19-R3 M12 열린 행 3개 유지(최종 리뷰는 fix wave, PR 은 머지 때, 전환 리허설은 사용자) · T19-R4 deny 에 `spot exec-key release`·`regime killswitch`·`ml promote|demote`·`evaluation rl promote|demote` 추가(CLAUDE.md 금지사항과 일치) · T19-R5 (a) env 접두 형태는 접두 deny 로 못 막는 알려진 한계(「M12 이후」) (b) 훅의 `"$CLAUDE_PROJECT_DIR/…"` 는 공식 문서 형태로 수용
- Ruling(M12 최종 리뷰):
  - FR-1: 잠금 보유면 daemon·app 모두 「이미 실행 중입니다 — <url>」 + exit 0, 무거운 boot 전에 잠금 확인(launchd `KeepAlive{SuccessfulExit:false}` 재기동 반복 방지)
  - FR-2: 로그온 등록 argv = `app --no-browser`(`bundle.service_argv`), app 도 FR-1 처리(포트가 열려 있고 `--no-browser` 가 아니면 브라우저만 열고 0)
  - FR-3: `Read(./secrets/**)` + Bash `secrets/.env*` 3형(백업 `.env.bak-*` 포함), Bash 로 templates 예시 읽기 과차단 수용, `regime killswitch` deny 는 `on`·`off` 로 분리
  - FR-4: 리허설 판정 = 그날 예정 시각 지난 잡 전부(주간 잡 fri·sat 은 `jobs run` 으로 1회), 플랜 파일은 손대지 않음
  - FR-5: `news_collect_request` 는 `deps.markets` 교집합만, 나머지는 `skipped`(`NewsCollectParams.market` 은 `Market` Literal)
  - FR-6: Minor M-1~M-8 + triage fix-in-wave(T3·T5·T6·T9·T10·T11·T18·T19 문서/테스트) 전부 이번 wave, defer 항목은 「M12 이후」
  - FR-7: Declined-to-judge 10줄 중 ④(runbook 리허설 saveticker 경합 한 줄)만 반영, 나머지는 옛 동작·기존 Ruling 유지
- M11 최종 리뷰에서 옮긴 이연(코드 미수정):
  - M8: `DELETE /jobs/{id}` 로 대기 중인 `approve_execute` 를 취소해도 승인 선점이 TTL 까지 남음 → spot `approvals.release` 훅 또는 409 (M12 Task 16 — `QueueKind.on_cancel` 이 `approvals.release`)
  - T5-R2(정정): approve 의 `DuplicateRequest` 분기는 202 기존 request_id 를 돌려주지 않는다 — 집행 요청을 못 넣었으므로 `approvals.release` 로 이 결정의 선점을 푼 뒤 409 를 유지한다(그 밖의 등록 실패도 선점 해제 후 재발생) (M12 Task 16)
  - 웹 거부 알림: 스펙 §8.10(e) '웹이 보내는 알림은 없다'와 충돌 → 큐 kind 또는 데몬 watcher 로(텔레그램 거부 알림은 fix wave 에서 구현, T5-R1) (M12 Task 16 — 큐 kind `approval_notice`, 데몬이 `[거부]` 발송)
- M11 에서 옮긴 비웹 이연: 섹터 자동분류 `_classify`·`auto_classify` 호출자(M5·M7; M12 Task 17 `ScanPort.classify`) · `_close_stale` 커널 헬퍼 통합(M7; M12 Task 14 `calls.close_stale`) · `metrics.pending_approvals/account_stance/stance_by_market`(M8; M12 Task 7 — 상태 절이 커널 `pending_approvals`·spot `stance.decide` 로 대체, evaluation 사본 없음) · spot monitor 의 단타 보유분 제외 검토(M9·M10; 「M12 이후」) · `InputsPort.recent`(scalp·fo 판정 뉴스·거시, fo `_novel_news` raw SQL; M12 Task 17) · FAKE 사본 잔여(regime `_turn`; M12 Task 14 `session.fake.fake_turn`) · `AccountsPort` dict 가짜 포트 정리(M12 Task 17 — `brokers()` 리스트 통일) · `views.account_summary` 의존 `test_performance_fo` 2건(「M12 이후」) · 커널 승격 후보 `reeval.handled_marker`·`_boot_notifier`·`_check_hm`(M10; M12 Task 14)·`_wrap_section`(「M12 이후」 — 레인 사본 허용)
- 이연(M0 리뷰):
  - `Lock` 주입 clock (M12 Task 14)
  - 실제 시그널 핸들러 테스트 격리 (M12 Task 14)
  - `start()` 실패 시 `_hb.stop()` (M12 Task 14)
  - `daemon` 명령 흐름 정리 (M12 Task 14)
  - `uvicorn` 지연 import (M12 Task 11)
  - `test_bundle` 단언 보강 (M12 Task 9·12)
  - `selfcheck` 가 DB 를 만드는 점 (M12 Task 13)
  - CLAUDE.md 금지 명령 "(M<N> 이후)" 표기·"WAL+30초" 중복 (M12 Task 19 — 두 표기 모두 없음 확인)
  - Windows `install` 로그온 확인(승계 — 「사용자 확인 대기」 번들 실기와 함께)

## M12 이후 — 다음 Phase 로 넘긴 것 (P9, 코드 미수정)

- 계좌 단위 일시정지 API(저장소·게이트 설계 필요; 지금은 전역 일시정지만)
- 옛 `/fo/positions` 부가 필드(`current`·`eval`·`budget_*`·`live_legs`)
- 단타 결정 상세(`scalp-<id>` 422 → 상세 링크 없음)
- spot monitor 의 단타 보유분 제외(옛 동작도 제외하지 않았음 → 동작 변경)
- `views.account_summary` 의존 `test_performance_fo` 단언 2개(대응 함수 없음)
- fo `_wrap_section`(레인이라 ingest 공유 불가 — 7줄 사본 허용)
- `ops_results`·`daily_markers` 테이블 승격(스펙 §2.4 — 지금은 control 플래그로 충분)
- 틱 단위 브로커 캐시(scalp·fo) · `settings.scalp.accounts` 계좌 키(`lane_of` 의 scalp 규칙)
- FAKE 세션 브로커 가짜 대체(`test_session_brokers_fake_fo` — 새 구조에 대응 경로 없음)
- 거시 관측 저장소(`/news/events` 거시)
- 실기(「사용자 확인 대기」 그대로): 번들 실빌드·더블클릭·SmartScreen·Gatekeeper · 텔레그램 버튼 · `tailscale_hosts/users/public_base` 실값 · KIS 환율(`FX@KRW` vs `FX@KRWKFTC`)
- T13-R2 P19 잔여: `trader --help` 가 아직 fastapi/starlette(기능 `routes` 모듈 수준 `APIRouter`, 16+8 모듈)·pandas(briefing→evaluation→ml 체인, 24 모듈)를 올린다 — 해소하려면 `Feature.routes` 팩토리화 + `feature.py` 지연 import(기능 계약 변경)
- T15: PWA PNG(먹색 ▲)와 `web/public/icon.svg`(파란 차트선) 불일치 → `icon.svg` 를 favicon 계열로
- T16: `expire_queued` 로 만료된 `approve_execute` 는 선점을 바로 풀지 않는다(TTL 뒤 `approvals_expire` 가 정리)
- T18: quotes `jobs._insert_name` 과 spot `ledger.upsert_ticker_name` 중복 → `ticker_names` 쓰기 helper 커널(`ledger/names.py`) 승격 후보
- 최종 리뷰 defer(코드 미수정):
  - T2: `kernel/ops/__init__` 공개 이름 3벌(`__all__`·지연 표·테스트) 일치 테스트 · `ops_check` 의 `notified` 가 `send` 반환값을 무시 · 오류 경로 항목(`{key}.error`)의 key·link
  - T3: health 가 매 점검 `load_ml` 재파싱 · quotes 영역 LIKE 의 `_` 이스케이프 · until 파싱 중복 · 로거 이름 혼재 · killswitch 항목 링크
  - T4: spot·fo health F2~F6 구조 정리 · US reconcile cron `days` 반영
  - T5: 날짜 모양 계좌 id 의 retention 판정 · `rmtree` 실패를 건수에 반영
  - T6·T7·T8 polish(상태 절·watchdog 문구·테스트 정리)
  - T9: 경로에 NUL 이 든 `ValueError` · `Popen` 래핑 · Windows `<Arguments>` 인용 · `first_run_init` 의 templates 전체 복사
  - T10: 트레이 `toggle_pause` 의 set_fn 경로/기본 경로 비대칭
  - T11 polish: `webbrowser` 실패 가드 · bind `0.0.0.0` 일 때 URL · 포트 점유 문구 · `WebThread._stopping` 경합
  - T12: smoke 스크립트 `IndexError` · spec 검사 · `sys.exit(main())` 형태
  - T13: playwright private API 의존 · frozen 분기 · `schema_version` 경계 · 손상 DB 의 JSON 출력
  - T14: HH:MM 엄격 파싱 · `Lock` 시계 주입 범위 · `hb_started` 테스트
  - T15: 큐 테스트 단언 · `_features_fp` glob · `latest_day` 사전순 비교
  - T16: CURRENCY import 위치 · `actor` 미사용 인자(`expire_queued` 선점은 위 T16 줄)
  - T17: naive datetime · SELECT 중복 · `classify` 매 호출 로드 · asof 벽시계 · `_failed` 잔존
  - T18: `ping` 의 boot · `names_fill` 이 `set_flag` 를 매 주기 쓰는 점(`_insert_name` 중복은 위 T18 줄)
  - T19-R5(a): env 접두(`FOO=1 uv run trader …`)는 `.claude/settings.json` deny 패턴으로 막지 못함 — 권한 훅이 필요

## 사용자 확인 대기 (옛 저장소 승계)

옛 저장소에서 아직 미확인인 실기 항목. 새 저장소 해당 기능이 이식된 뒤 확인한다.

- [ ] 실기(사용자 확인 대기, 영업일 09:00~15:30 KST): KIS 주문 테스트 — `trader accounts order-test --account <id>`(모의 계좌) → 1주 지정가(현재가 −10%) 접수·취소 JSON 확인 (M12 Task 18 이후)
- [ ] 실기(사용자 확인 대기): 텔레그램 승인 — 모드를 `approve` 로 두고 BUY 결정 때 승인 요청 → 텔레그램 `/approve <id>` → 주문 접수 확인 → 모드 `auto` 원복 (M2·M4 이후)
- [ ] 사용자 확인 대기: `secrets/.env` 의 `WEB_TOKEN`(`trader app`·`trader web` 기동이 없으면 자동 생성) → 시스템 화면 「이 PC 에서 받기」 또는 토큰 입력 → 일시정지/재개가 반영되는지, 승인 요청이 있을 때 웹·텔레그램 버튼 승인 → 주문 접수 확인 (M12 전환 뒤 — 백엔드는 M11, 화면은 M11b)
- [ ] 사용자 확인 대기: `settings.web.tailscale_hosts`·`tailscale_users`·`public_base` 실제 값 설정 → 폰에서 tailnet 이름으로 조회 200·허용 밖 로그인 403·키 입력 403 확인 (M12 전환 뒤)
- [ ] 실기(사용자 확인 대기): 번들 zip(`v0.2.1` 태그 `build.yml`; `v0.2.0` 은 기능 README 누락으로 스모크 실패)을 풀어 더블클릭 → 웹 UI → `./trader accounts ping` (macOS `xattr`/우클릭 열기·Windows SmartScreen 1회; Windows `install` 로그온 등록 확인 포함) (M12 이후)
- [ ] 실기(사용자 확인 대기): `Trader.app` 더블클릭 → 메뉴바 아이콘·브라우저 → 메뉴에서 일시정지/재개·로그 폴더·종료(실행 중 세션 대기 후 잠금 해제). Windows 는 `TraderApp.exe` 작업표시줄 아이콘·콘솔 창 없음 확인 (M12 이후)
- [ ] 실기(사용자 확인 대기, 모의): KR 잔고 응답 `output2.tot_evlu_amt`·`nass_amt`·종목 `ord_psbl_qty` 필드 존재, US 잔고·체결이 NASD·NYSE·AMEX 3회 조회로 합쳐지는지, 재부팅 뒤 `daemon.lock` 이 stale 로 넘겨지는지 (M5 이후)
- [ ] 사용자 확인 대기: `channels.naver_blog` 기본값 — 옛 기본 `ranto28` 복원 여부 결정 (M3)
- [ ] 사용자 확인 대기: `EDGAR_USER_AGENT` 시크릿 입력 (M3 ingest, EDGAR 수집)
