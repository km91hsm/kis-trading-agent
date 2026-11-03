# regime

## 책임

- 시장 국면·킬스위치·권리 일정 블랙아웃(tier=service). 국면·킬스위치·블랙아웃 판정은 전부 파이썬 규칙이다.
- 설정: `settings.yaml` 의 `hedge`·`event_blackout`(`settings.py`: `HedgeCfg`·`HedgeInstrument`·`BlackoutCfg`).
- 원장 v10 `market_views`(`migrations.py`): 날짜·시장당 1행, 시장 세션 제안(stance·헤지 비중·레버리지·확신)과 성숙 결과(`matured_at`·`index_return`·`hit`).
- 국면(`regime.py`): 3지표(200일선·모멘텀·변동성) + 보조(전략가 RISK_OFF·KR 외인 수급) → `bull|neutral|bear`, bear 해제는 히스테리시스. 상태는 control `regime:<market>` 한 곳.
- 킬스위치(`killswitch.py`): (계좌·시장) NAV 가 기준 대비 `risk.killswitch_drawdown_pct` 넘게 빠지면 control `killswitch` 를 켠다. 자동 발동은 발동 계좌만, 수동(`/kill`·CLI)은 전 계좌. 해제는 사람만(원인 메모 `MIN_NOTE` 자 이상). 기준은 `killswitch_base:<acct>:<m>`, 입출금 의심은 `killswitch_flow:<acct>:<m>`(기록만).
- 블랙아웃(`blackout.py`): 예탁원 기준일을 control `ksd_events:KR` 에 종류별 마지막 성공분으로 합쳐 두고, `event_blackout.enabled` 면 기준일 `before_days` 전 ~ `after_days` 뒤 신규 매수 사유를 낸다(국내만, 기본 끔).
- 포트(`ports.py`): `PORT = Port("regime", build_port)` → `RegimePortImpl`(커널 `RegimePort` 전부). 레인은 `Boot.ports["regime"]` 로 국면(헤지 꺼짐·플래그 없음 → bull)·오늘 시장 판단(`market_view:<m>` 날짜 일치)·헤지 종목/상품·비중 clamp·킬스위치 사유/관측·블랙아웃 사유를 받는다.
- 문구(`messages.py`): `regime_change`·`market_view`·`killswitch_on`·`ksd_stale`. 코드값은 커널 `REGIME`·`STANCE` 표로 한글.

## 규칙

- 코드는 `trader.kernel.*` 와 다른 기능의 `api.py` 만 import 한다(3층 규칙). 레인(spot)은 이 기능을 import 하지 않고 `ports["regime"]` 로 받는다.
- 로거 `trader.regime`. 저장 시각은 UTC ISO8601, 날짜 기본값은 KST.

## 공개면 (`api.py`)

`HedgeCfg`·`HedgeInstrument`·`BlackoutCfg`·`load`(국면 플래그)·`core_count`·`killswitch_reason`·`blackout_reason`.

## 테스트

`tests/features/regime/`(포트는 `test_ports.py`, 현물 게이트 배선은 `tests/features/spot/test_executor_regime.py`), KIS 권리 일정 TR 파싱은 `tests/kernel/broker/test_market_ksd.py`.

## 알려진 차이 (옛 저장소 대비)

1. `regime.update`·`reapply` 의 `now` 기본값이 `datetime.now(UTC)` 다(옛 코드는 naive `datetime.now()`). `updated_at` 에 오프셋이 붙는다.
2. `regime.summary` 는 옛 지연 import(`orchestrator.tools.market_view_today`·`market_view.scorecard`) 대신 `view_fn`·`scorecard_fn` 을 받는다. 주지 않으면 같은 기능의 `market_view.load_view`·`scorecard` 를 쓰고, 그 모듈이 없으면 `view`·`scorecard` 는 None(Ruling P1).
3. 킬스위치 기준·해제의 NAV 는 `positions_snapshot.nav` 대신 `account_nav.nav` 를 읽는다(새 원장에서 NAV 는 `account_nav` 에 있다).
4. `messages.ksd_stale` 는 `(라벨, 일수)` 목록 대신 `stale_kinds` 결과 dict 를 받아 종류를 `LABEL` 로 바꾼다. 해제 안내 명령은 `trader control killswitch off` → `trader regime killswitch off`.
5. 블랙아웃 로거는 `trader.risk` → `trader.regime`.
