# regime

## 책임

- 시장 국면·킬스위치·권리 일정 블랙아웃(tier=service). 국면·킬스위치·블랙아웃 판정은 전부 파이썬 규칙이다.
- 설정: `settings.yaml` 의 `hedge`·`event_blackout`(`settings.py`: `HedgeCfg`·`HedgeInstrument`·`BlackoutCfg`).
- 원장 v10 `market_views`(`migrations.py`): 날짜·시장당 1행, 시장 세션 제안(stance·헤지 비중·레버리지·확신)과 성숙 결과(`matured_at`·`index_return`·`hit`).
- 국면(`regime.py`): 3지표(200일선·모멘텀·변동성) + 보조(전략가 RISK_OFF·KR 외인 수급) → `bull|neutral|bear`, bear 해제는 히스테리시스. 상태는 control `regime:<market>` 한 곳.
- 킬스위치(`killswitch.py`): (계좌·시장) NAV 가 기준 대비 `risk.killswitch_drawdown_pct` 넘게 빠지면 control `killswitch` 를 켠다. 자동 발동은 발동 계좌만, 수동(`/kill`·CLI)은 전 계좌. 해제는 사람만(원인 메모 `MIN_NOTE` 자 이상). 기준은 `killswitch_base:<acct>:<m>`, 입출금 의심은 `killswitch_flow:<acct>:<m>`(기록만).
- 블랙아웃(`blackout.py`): 예탁원 기준일을 control `ksd_events:KR` 에 종류별 마지막 성공분으로 합쳐 두고, `event_blackout.enabled` 면 기준일 `before_days` 전 ~ `after_days` 뒤 신규 매수 사유를 낸다(국내만, 기본 끔).
- 포트(`ports.py`): `PORT = Port("regime", build_port)` → `RegimePortImpl`(커널 `RegimePort` 전부). 레인은 `Boot.ports["regime"]` 로 국면(헤지 꺼짐·플래그 없음 → bull)·오늘 시장 판단(`market_view.load_view`)·헤지 종목/상품·비중 clamp(`market_view.clamp_weight`)·킬스위치 사유/관측·블랙아웃 사유를 받는다.
- 시장 세션(`market_view.py`): 입력 조립 `build_inputs`(국면·지수 proxy 60봉·거시·저장소/구글 거시 뉴스·시장 메모·세이브티커·헤지 보유·모의 계좌 섹터 비중, 절별 오류 격리 `errors`) → `_market_inputs` → 세션 1회 `run_market_view`(`hedge.enabled and hedge.market_session` 아니면·상한이면 생략) → `_market_view` 읽기. 제안은 원장 `market_views` 에 남기고(`record_view`) 만기 뒤 지수 수익으로 채점한다(`mature_views`·`scorecard`). `market_sessions_today` 는 `llm.market_session_cap` 용.
- 세션 종류(`session_kinds.py`): `MARKET`(kind·timeout·runs `market`, 추가 상한 `llm.market_session_cap`, hard 감속). 러너 매핑은 `agents_for_runner()`(AGENTS + 별칭 `orchestrator`).
- 도구(`tools.py`·`tool_cli.py`): `trader tool market bundle|write|summarize`·`trader tool submit-market-view`. 전제조건(`_market_inputs` → `_market_analyst` → 토론 → `_market_summary` → 제출)·`debate_rounds` 상한·파일명↔페이로드 일치는 도구가 강제한다. 제출은 `_market_view` + control `market_view:<m>`(날짜 포함) + `market_views`.
- 스키마(`schemas.py`): `HedgeProposal`(stance·hedge_weight ≤0.25·leveraged·scalp_ok·confidence·horizon_days·rationale ≤500·invalidation_conditions). 리포트·턴은 커널 공용.
- 에이전트(`agents/`): `analyst-market`·`market-researcher-bull`·`market-researcher-bear`·`strategist`·`market-orchestrator`(market 모드 절만).
- FAKE(`fake.py`): `FAKE_CLAUDE=1` 이면 `run_fake_market(ctx, spec)` → `write_fake_market` 가 같은 도구로 NEUTRAL 리포트·토론 r0(+r1)·요약·제안(bear → RISK_OFF 0.10, 그 외 RISK_ON 0.0)을 쓴다.
- 입력(`inputs.py`): `proxy_candles`(지수 proxy 완성 일봉, `market_hours.completed_candles`)·`vix`(US, `FRED_API_KEY`)·`flow_weak_signal`(KR 외인 수급, `hedge.flow_signal`).
- 잡(`jobs.py`): `{market}_regime`(cron `schedule.yaml regime.<m>`, 평일, 게이트 market, `hedge.enabled` 일 때) = `regime_update` → `market_view_body`(`hedge.market_session`) → `apply_view` → `mature_views`, 시장 세션·재판정 예외는 격리. `ksd_events`(cron `ksd_events`, 평일 07:05 KST, `event_blackout.enabled` 일 때) = `ksd_body`. 시세·계좌·브로커는 `deps.ports["accounts"]`, 시장 세션 `LaneContext` 는 `state=deps.state`·`ext["secrets"]`·`cancel=deps.stop_event`.
- 큐 kind(`jobs.QUEUE_KINDS`): `market_view`(`{market: KR|US}`, slow 레인, 라벨 "시장 세션") = `market_view_body`.
- CLI(`cli.py`): `trader regime show`(헤지 설정 + `summary` JSON)·`backtest --market KR|US --days 260~1000`·`killswitch on [--reason]|off --note <MIN_NOTE 자 이상>|show`.
- 텔레그램(`telegram.py`): `/kill`(켜기만, 신뢰 발신자만) → `killswitch.engage(source="telegram")` + `killswitch_on` 문구. `/status` 절 「국면」 = `regime.line`.
- 설정(schedule): `regime`(`RegimeSchedule`: kr "08:25" KST, us 09:20 America/New_York, 값은 "HH:MM" 또는 `AtTime{time, tz, days}`)·`ksd_events`("HH:MM", KST).
- 문구(`messages.py`): `regime_change`·`market_view`·`killswitch_on`·`ksd_stale`. 코드값은 커널 `REGIME`·`STANCE` 표로 한글.

## 규칙

- 코드는 `trader.kernel.*` 와 다른 기능의 `api.py` 만 import 한다(3층 규칙). 레인(spot)은 이 기능을 import 하지 않고 `ports["regime"]` 로 받는다.
- 로거 `trader.regime`. 저장 시각은 UTC ISO8601, 날짜 기본값은 KST.

## 공개면 (`api.py`)

`HedgeCfg`·`HedgeInstrument`·`BlackoutCfg`·`load`(국면 플래그)·`core_count`·`killswitch_reason`·`blackout_reason`. 시장 세션 산출은 레인이 `ports["regime"]` 로 받는다.

## 테스트

`tests/features/regime/`(잡·큐는 `test_jobs.py`, CLI `test_cli.py`, `/kill`·상태 절 `test_telegram.py`, 시장 세션은 `test_market_view.py`·`test_market_tools.py`, 포트는 `test_ports.py`, 현물 게이트 배선은 `tests/features/spot/test_executor_regime.py`), KIS 권리 일정 TR 파싱은 `tests/kernel/broker/test_market_ksd.py`.

## 알려진 차이 (옛 저장소 대비)

1. `regime.update`·`reapply` 의 `now` 기본값이 `datetime.now(UTC)` 다(옛 코드는 naive `datetime.now()`). `updated_at` 에 오프셋이 붙는다.
2. `regime.summary` 는 옛 지연 import(`orchestrator.tools.market_view_today`·`market_view.scorecard`) 대신 `view_fn`·`scorecard_fn` 을 받는다. 주지 않으면 같은 기능의 `market_view.load_view`·`scorecard` 를 쓰고, 그 모듈이 없으면 `view`·`scorecard` 는 None(Ruling P1).
3. 킬스위치 기준·해제의 NAV 는 `positions_snapshot.nav` 대신 `account_nav.nav` 를 읽는다(새 원장에서 NAV 는 `account_nav` 에 있다).
4. `messages.ksd_stale` 는 `(라벨, 일수)` 목록 대신 `stale_kinds` 결과 dict 를 받아 종류를 `LABEL` 로 바꾼다. 해제 안내 명령은 `trader control killswitch off` → `trader regime killswitch off`.
5. 블랙아웃 로거는 `trader.risk` → `trader.regime`.
6. 시장 세션 에이전트 이름: 옛 공용 `researcher-bull`·`researcher-bear`·`orchestrator` 대신 `market-researcher-bull`·`market-researcher-bear`·`market-orchestrator`(에이전트 이름 전역 유일). 모델 키도 `models.market_researcher_bull|bear` 로 바뀌어 옛 `researcher_bull|bear` 설정을 따르지 않는다(없으면 md 기본 sonnet). 커널 프롬프트 조립용으로 러너 매핑에만 별칭 `orchestrator` 를 둔다.
7. 시장 세션 도구 컨텍스트는 `tools.MarketToolContext(settings, conn, state, now)` 다(옛 공용 `ToolContext` 의 계좌·브로커 없음). `build_inputs`·`run_market_view` 는 `LaneContext` 를 받고, 거시 키는 `ctx.ext["secrets"]`(없으면 키 없음)에서 읽는다. 세션 취소는 `run_market_view(cancel=)` → `run_session(cancel=)`.
8. 세이브티커 폴더는 `sources.yaml` 의 `news.saveticker_dir` 대신 `saveticker.dir`(`saveticker.api.load_cfg`) 를 본다.
9. 섹터 비중(`_sector_weights`)은 옛 `ledger/views.sector_weights` 를 이 모듈 private 로 옮겼다. 포지션은 `kernel.execution.ledger.latest_positions`.
10. 옛 데몬은 국면 갱신·시장 세션·재판정을 `<시장>_scan_analyze` 앞에서, 제안 채점을 reflect 잡에서 돌렸다. 이제 `{market}_regime` 한 잡이 순서대로 돌고(scan 은 M7), 채점(`mature_views`)도 이 잡 끝에서 모든 시장을 본다.
11. 킬스위치 CLI 는 `trader control killswitch on|off|show [--note]` → `trader regime killswitch on [--reason]|off --note|show`. 켜는 사유는 `--reason`(옛 `--note` 겸용 아님), off 의 짧은 메모는 원장을 열기 전에 거부한다.
12. `regime backtest` 의 시세 클라이언트는 `cli._build_market(secrets)` 대신 계좌 포트(`_market_client(boot)` → `ports["accounts"].market_client()`)이고, 시장 검사를 설정 로드 앞에서 한다.
13. `/kill` 해제 안내는 `trader regime killswitch off`(옛 `control killswitch off`).
