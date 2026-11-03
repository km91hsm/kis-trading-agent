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
- 문구(`messages.py`): `regime_change`·`market_view`·`killswitch_on`·`ksd_stale`. 코드값은 커널 `REGIME`·`STANCE` 표로 한글.

## 규칙

- 코드는 `trader.kernel.*` 와 다른 기능의 `api.py` 만 import 한다(3층 규칙). 레인(spot)은 이 기능을 import 하지 않고 `ports["regime"]` 로 받는다.
- 로거 `trader.regime`. 저장 시각은 UTC ISO8601, 날짜 기본값은 KST.

## 공개면 (`api.py`)

`HedgeCfg`·`HedgeInstrument`·`BlackoutCfg`·`load`(국면 플래그)·`core_count`·`killswitch_reason`·`blackout_reason`. 시장 세션 산출은 레인이 `ports["regime"]` 로 받는다.

## 테스트

`tests/features/regime/`(시장 세션은 `test_market_view.py`·`test_market_tools.py`, 포트는 `test_ports.py`, 현물 게이트 배선은 `tests/features/spot/test_executor_regime.py`), KIS 권리 일정 TR 파싱은 `tests/kernel/broker/test_market_ksd.py`.

## 알려진 차이 (옛 저장소 대비)

1. `regime.update`·`reapply` 의 `now` 기본값이 `datetime.now(UTC)` 다(옛 코드는 naive `datetime.now()`). `updated_at` 에 오프셋이 붙는다.
2. `regime.summary` 는 옛 지연 import(`orchestrator.tools.market_view_today`·`market_view.scorecard`) 대신 `view_fn`·`scorecard_fn` 을 받는다. 주지 않으면 같은 기능의 `market_view.load_view`·`scorecard` 를 쓰고, 그 모듈이 없으면 `view`·`scorecard` 는 None(Ruling P1).
3. 킬스위치 기준·해제의 NAV 는 `positions_snapshot.nav` 대신 `account_nav.nav` 를 읽는다(새 원장에서 NAV 는 `account_nav` 에 있다).
4. `messages.ksd_stale` 는 `(라벨, 일수)` 목록 대신 `stale_kinds` 결과 dict 를 받아 종류를 `LABEL` 로 바꾼다. 해제 안내 명령은 `trader control killswitch off` → `trader regime killswitch off`.
5. 블랙아웃 로거는 `trader.risk` → `trader.regime`.
6. 시장 세션 에이전트 이름: 옛 공용 `researcher-bull`·`researcher-bear`·`orchestrator` 대신 `market-researcher-bull`·`market-researcher-bear`·`market-orchestrator`(에이전트 이름 전역 유일). 모델 키도 `models.market_researcher_bull|bear` 로 바뀌어 옛 `researcher_bull|bear` 설정을 따르지 않는다(없으면 md 기본 sonnet). 커널 프롬프트 조립용으로 러너 매핑에만 별칭 `orchestrator` 를 둔다.
7. 시장 세션 도구 컨텍스트는 `tools.MarketToolContext(settings, conn, state, now)` 다(옛 공용 `ToolContext` 의 계좌·브로커 없음). `build_inputs`·`run_market_view` 는 `LaneContext` 를 받고, 거시 키는 `ctx.ext["secrets"]`(없으면 키 없음)에서 읽는다. 세션 취소(`cancel`) 인자는 없다.
8. 세이브티커 폴더는 `sources.yaml` 의 `news.saveticker_dir` 대신 `saveticker.dir`(`saveticker.api.load_cfg`) 를 본다.
9. 섹터 비중(`_sector_weights`)은 옛 `ledger/views.sector_weights` 를 이 모듈 private 로 옮겼다. 포지션은 `kernel.execution.ledger.latest_positions`.
