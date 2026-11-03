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
  - (M7 회수 완료): `_news_trigger`·`_discover_body`·`_disclosure_trigger`·`_events_check`·`ticker_names` 캐시·`peer_fn`·`universe check|sectors`, 옛 `test_daemon_news` 7건·`test_daemon_disclosure` 3건
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
  - (M7 회수 완료; RL 은 M8, `AnalystRole` quant 복원은 Ruling M7-K1 로 하지 않음): quant·theme
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
- [x] PR·CI(ubuntu+windows)·머지 (#6, main a12d41f)
- 이연(M2, M5 가 한 것): 체결통보 리스너(`_notice_listeners`)
- 이연(M5):
  - M6: killswitch·hedge·`cash_park`·`excluded_tickers`(monitor 의 헤지/현금 운용 건너뛰기·`cancel_buys`)
  - (M7 회수 완료): 공시/뉴스 트리거 생산(`news.post_ingest` 훅)·`analyze_ticker` kind(종목 락 `locks.lock_for` 공유)·discover·`reevaluate` 의 dry_run 담긴 요청 실행 단언·US 거래소 보강 단언
  - M8: memory(메모리 브레이커 인자·`test_resume` memory 1건)·`close` 무효화 종가표(`close_table` 콜러블 주입, Ruling 14)
  - M12: `ops_check` 잡·health(`kernel/ops`, M9 플랜 A15 에서 M12 로 옮김)
  - M10: fo 분기·`test_balance_dispatch` 3건·`test_fills_fo`
  - M11: jobs 라우터(웹 큐 API)·섹터 자동분류(`_classify`)·웹 시세 허브 `hub_blocked` 비켜서기(live_quotes yielding)
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
  - M10: 커널 `latest_positions` 에 asset 필터 없음 — fo 행 생기면 시장 세션 입력에 파생 포지션 섞임; spot `_latest_positions` 와 통합
  - M10(fo 이식 때): FAKE 헬퍼·검증/요약 헬퍼의 spot/regime 중복 → `kernel.session` 승격
  - M11: 웹 `/api/system/theme`(옛 `test_daemon_theme::test_api_theme`)
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
  - M11: 웹 `/api/features`·`/api/ml`·`RunJobButton`
  - M12: `trader status` ML 절·ops.health `disclosure` 영역(`test_health_disclosure_area_critical_for_held`)
  - M11: 섹터 상한은 yaml 에 없는 종목(유니버스 밖 발굴)에는 적용되지 않음 — `auto_classify` 호출자는 M11
  - M8·M11 이후: `scan_analyze._close_stale`·spot `_close_stale_sessions` 를 커널 공개 헬퍼로 통합
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
- Ruling(M8 플랜): A1 RL 사이저는 evaluation `rl/`(ml→evaluation 순환 방지; `rl_train` 독립 cron 토 10:30) · A2 레인 접점은 `ports["evaluation"]` 하나 · A3 `memory_entries` 에 `realized_return`·`alpha_vs_benchmark` 없음(스펙 §2.4) · A5 `round_trip_bp` 커널 승격(M7 P1 해소) · A6 `MonitorContext.close_table` 주입(M5 Ruling 14) · A7 `features_snapshot` 유지 + reflect 재스냅샷 · A8 retention prune 은 M12, fills 선동기화 없음 · A10 `usage_probe` 커널 내장 interval(1800 고정) · A13 CLI 는 `trader evaluation …` 만(최상위 별칭은 M12) · A15 `test_performance_fo` 는 M10
- Ruling(M8, 실행 중 추가): T2-R1 `quick.ask_json` 은 `agents.with_schema` 재사용 · T9-R2(T9-R1 뒤집음) `readiness.interval_kinds_of` 는 interval 잡 `kind` 수집(커널 잡 큐·내장 잡 포함) · `store.held_at` 은 `account_nav` 만 있는 날을 보유 없음으로 · 집행 브레이커는 계좌 자신의 30일 NAV(paper 다수면 마지막) · `_rl_record` 는 exec-key 선점 뒤(선점 실패 시 `applied=0`)
- 이연(M8): M10 `test_performance_fo` 6건·fo `nav_series` stub · M11 `/api/performance`·`/api/rl`·`/api/memory`·`lots` CSV·`metrics.pending_approvals/account_stance/stance_by_market` · M12 reflect 의 `retention.prune`·status/health 보정 재점검·최상위 CLI 별칭 결정·`usage_probe.interval_min` 반영

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
- [ ] PR·CI(ubuntu+windows)·머지
- Ruling(M9 플랜): A1 주문 경로는 spot 이 노출하는 `stock_exec` 포트(execute·cancel·sync_fills; 게이트 복제 기각) · A2 `scalp` origin 은 spot ORIGINS 유지 · A3 JSON 상태 파일 유지·마이그레이션 없음(legacy 테스트 1건 미이식) · A4 잡 `{market}_scalp` kind `scalp` interval `{market}.monitor.interval_s`·게이트 `none`(본문이 창·휴장·일시정지 거르고 outbox flush) · A5 판정자 `quick.ask_json` + `session_allowance(kind="scalp_judge")` · A6 후보 = `_scan` 체크포인트 + `job_requests(source=news)` + `ScanPort.watchlist` · A8 모의 전용 `skip_accounts`, `block_reason` 커널 승격 · A9 `ScalpPort.open_tickers` 로 리밸런스 제외 · A10 `SCALP_EXIT` 에 `carry_over` 추가 · A12 `event.at` 은 주입 시계 · A13 커널 `status_message` 절 격리 · A14 `_kind` 가 `_entry/_exit` 꼬리 제거 · A15 `OPEN_STATUSES` 커널, `DEAD_STATUSES` import, `timeout_by_kind` 기본 `market: 600`, `RebalanceParams`, `market_view trading=False` 유지, `ops_check`/health 는 M12 · A16 판정 컨텍스트 뉴스는 빈 목록(`InputsPort.recent` 는 M11/M12 후보)
- Ruling(M9, 실행 중 추가): P1 `timeout_by_kind` 기본값 테스트는 `tests/kernel/config/test_llm_settings.py` · T4-R1 `judge_context.regime` 은 `ports["regime"].load` 저장 레코드를 그대로(없음 → `{state: None, count: None}`; 플랜 A5/A6 의 `state()/core_count()` 문구 대체) · T5-R1 `max_positions` 는 `allowed()` 가 아니라 진입 루프가 open+pending 으로 센다(옛 동작, 플랜 A7 정정) · T6-R1 커널 `core.cancel` 은 잔량(`qty − SUM(fills.qty)`)으로 취소, 잔량 0 이면 브로커 호출 없이 `ok False` "남은 수량 없음(전량 체결)"(spot US 취소에도 적용, KR 은 `QTY_ALL_ORD_YN=Y` 라 무영향) · T7-R1 `SCALP_TICK.gate="none"`, 본문이 `ctx.gates["monitor"]` 를 런타임에 재사용(창 밖·주말·휴장)·`accounts.paused` 직접 확인·건너뛴 모든 경로에서 outbox flush, `exchanges={}`
- 이연(M9):
  - M10: FAKE 헬퍼 `kernel.session` 승격 · 커널 `latest_positions` asset 필터
  - M11: 웹 `/system/scalp` 자원·SSE `scalp` 키 · `InputsPort.recent`(판정 컨텍스트 뉴스, 지금 `news=[]`)
  - M12: `ops_check`/health · `trader status` 단타 절 CLI 점검
  - 테스트 보강: US 시장 테스트(`exchange_of` 폴백·`analyze_targets` 거래소) · `StockExecPortImpl.cancel` broker None 가드 · 커널 tradability 테스트의 spot 헬퍼 import 정리 · `test_state` 종목명 리터럴 합성화

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
