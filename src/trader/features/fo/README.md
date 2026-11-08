# fo

## 책임

- 선물옵션 방향성 레인(tier=lane): 코스피200 선물 롱/숏·옵션 콜/풋 매수·매도. **모의 파생 계좌 전용·기본 끔**(`settings.yaml fo.enabled: false`). `enabled` 는 신규 진입만 막고 보유분 감시·축소는 계속한다. 실전 파생 계좌는 항상 `off`(주문·취소 없음, 기록만).
- 현물 파이프라인과 같은 모양(수집 → 세션 → 파이썬 게이트 → 주문 → 장중 감시·재평가 → 기록)이되, 주문은 **자체 실행기**(`executor.py`)만 낸다. 에이전트는 방향·수단·확신·손절선을 제안하고 수량·가격·허용 여부는 파이썬 게이트(`rules`·`sizing`)가 정한다.
- 설정: `settings.yaml fo`(`FoCfg` + 하위 모델 6종, 상품 모델은 커널 `broker.models.FoProduct`), `schedule.yaml fo`(`FoSchedule`: `kr.session` 08:20 · `kr.monitor` 08:45~15:45 60s).
- 원장: 마이그레이션 14 `fo` — `fo_positions`·`fo_order_legs`(`request_id` 포함)·`fo_decisions`·`fo_nav_snapshot`·`fo_positions_snapshot`.
- 레인이라 다른 기능을 import 하지 않는다. 필요한 것은 포트(`FoExecContext.ports`, deps 주입)로 받는다. 에이전트(애널리스트·리서처)는 커널 레지스트리에서 이름으로 재사용한다.

## 포트

| 포트 | 메서드 | 용도 |
|---|---|---|
| `accounts` | `fo_accounts()`·`fo_brokers()` | 파생(asset fo) 계좌와 그 브로커(현물 접근자는 fo 를 제외) |
| `accounts` | `accounts()`·`brokers()` | 현물 계좌·브로커(`brokers()` 는 list) — 헤지 보유 읽기 |
| `accounts` | `market_client()`·`holiday_fn()` | 시세 클라이언트(실전 env)·휴장 판정 함수 |
| `accounts` | `policy(conn, account)`·`paused(conn)` | 실효 모드(실전 파생 항상 off)·일시정지 |
| `regime` | `load`·`view_today`·`hedge_tickers`·`killswitch_reason` | 국면 저장 레코드·오늘 시장 뷰·헤지 종목·킬스위치 사유 |
| `inputs` | `recent` | `news_macro` 절 시장 뉴스·엔진 `_novel_news`(없으면 이연 오류·raw SQL 폴백, M12 T17) |

내놓는 포트·공개면은 없다(`api.py` 는 `__all__ = []`).

## CLI

`trader fo …`(`cli.py`, 옵션·출력 JSON 은 옛과 같다). `FAKE_CLAUDE=1` 없이 `session` 을 돌리지 않고 `monitor|close|order-test` 는 사용자 전용이다.

| 명령 | 설명 |
|---|---|
| `contracts [--product mini\|regular --today …]` | 마스터에서 앞월물 선물·옵션 후보 조회(주문 없음) |
| `chain [--product … --strikes 4 --today …]` | 옵션 체인 ATM ± N 시세(주문 없음) |
| `verify --account … [--vehicle …]` | 마스터·코스피200·선물 시세·주문가능·잔고를 모두 조회해 실제 보유 행으로 확인된 수단만 `fo_verified` 플래그를 쓴다(조회 전용, 한 단계라도 실패하면 플래그 없이 exit 1) |
| `session [--dry-run --asof …]` | 아침 판정 세션 1회(`session.run`). 파생 계좌 없음·오늘 `_fo_decision` 있음·`enabled=false ∧ 보유 없음`·`session_cap` 도달이면 `skipped` JSON |
| `status` | `status.lines` 한 묶음(원장만 읽음) — 텔레그램 `/fo`·`/status` 「파생(선물옵션)」 절과 같은 줄 |
| `monitor [--once --dry-run --interval 60]` | 모니터 엔진 틱(`engine.tick`) 반복 — **주문이 나가므로 사용자 전용** |
| `adopt --account … --position … --side LONG\|SHORT --kind FUTURES\|CALL\|PUT --qty … --stop-pt …` | `unknown` 포지션을 사람이 확인해 `held` 로 승계(소유 계좌 일치 필수) |
| `close --account … --position … --confirm CLOSE` | 포지션 수동 청산 — **주문이 나가므로 사용자 전용** |
| `order-test --account … --vehicle FUTURES\|OPTION_BUY\|OPTION_SELL [--wait 30]` | 실기 2 검증: 1계약 진입 → 체결 확정 → 잔고 행 확인 → 즉시 청산. 실전 계좌 거부, **사용자 전용** |

## 데몬 잡

전부 KR 전용·`per_market=False`·`gate="none"` — 커널 `monitor` 게이트는 `kr.monitor` 창에 묶여 15:30~15:45 를 놓치고 `market` 게이트는 `job.market` 이 필요하다. 본문이 파생 계좌 유무·일시정지·주말·휴장(`should_skip`)·파생 장(`is_fo_open`)을 직접 본다. 모니터만 일시정지로 건너뛰지 않는다(위험 청산은 계속).

| 잡 | kind | trigger | schedule_key | gate | 비고 |
|---|---|---|---|---|---|
| `kr_fo_session` | `fo_session` | cron(평일) | `fo.kr.session`(08:20) | none | heavy, catch_up notify. 08:45 개장 전이라 파생 장 시계는 보지 않는다 |
| `kr_fo_monitor` | `fo_monitor` | interval(기본 60s) | `fo.kr.monitor.interval_s` | none | start_soon 30s, catch_up none. 창 = `is_fo_open ∧ 모니터 창`(평일·비휴장). 일시정지 중에도 돈다 — 손절·만기·축소·회수·정산 전이는 진행, 트리거·재평가·보류 진입·REPLACE 는 기록만 |
| `kr_fo_reconcile` | `fo_reconcile` | cron(평일) | `reconcile.kr` | none | misfire idempotent, catch_up queue. `positions.recover` + 대조 알림 |

## 큐 kind

| kind | 파라미터 | lane | 비고 |
|---|---|---|---|
| `fo_session` | 없음 | slow | 파생 아침 세션 요청. 상한 초과는 `skipped` |
| `fo_reevaluate` | `account_id·position_id·expected_version·request_id·round·trigger_id·date` | reeval(우선순위 10) | 엔진이 트리거·요청 체크포인트를 남긴 뒤 넣는다. 트리거가 이미 처리됐거나 버전·상태가 바뀌었으면 세션 없이 `폐기:` |
| `fo_close` | `account_id·position_id` | fast(우선순위 10) | 사람 요청. 실행 직전 장 시간·계좌·상태를 다시 보고 모드(off)·dry_run 은 실행기가 기록만으로. 수동 청산은 위험 축소라 일시정지 중에도 낸다 |
| `fo_reconcile` | 없음 | fast | `kr_fo_reconcile` 의 catch_up 대상(spot `fills_sync` 선례, 실행 중 추가) |

## 세션 종류·에이전트

- `fo`: 아침 판정(analyst-technical·analyst-sentiment·analyst-market → researcher-bull/bear 토론 → `fo-trader`). 스키마 `AnalystReport`·`DebateTurn`·`FoDecisionEnvelope`, `runs.kind='fo'`, throttle hard.
- `fo_resume`: 보유 재평가(stdin 에 `position`·`trigger`). 스키마 `FoDecisionEnvelope`, `runs.kind='monitor'`(현물 `resume` 과 같은 등급), throttle soft(80% 감속에서도 보유 재평가는 통과).
- md 는 `agents/fo-trader.md`·`agents/fo-orchestrator.md` 둘뿐. 나머지는 `session_kinds.agents_for_runner()` 가 레지스트리 `discover()` 로 이름으로 모으고, 별칭 `orchestrator` → `fo-orchestrator.md` 를 둔다(A6).
- 상한 `fo.session_cap` 은 커널이 모르는 설정이라 `extra_cap_key=None` 이고 `session.run` 이 세션 앞에서 오늘 `runs.kind='fo'` 건수를 검사한다. 초과는 `session.run` 이 `PipelineError` 를 잡아 `skipped` 로 돌려주고, 잡·큐·CLI 는 그 결과를 그대로 기록/출력한다.

## 도구

- `trader tool fo bundle|write|summarize`: 쓰기 허용은 `_fo_analyst_<technical|sentiment|news_macro>`(AnalystReport, 파일명 role 일치)·`_fo_debate_r<n>_<bull|bear>`(DebateTurn, round·side 일치, n ≤ debate_rounds)뿐이고 `_fo_inputs` 가 없으면 쓸 수 없다(`FO_WRITE_RULES`: `_fo_analyst_*`·`_fo_debate_r*_*` 는 `_fo_inputs` 필요). 토론 턴을 새로 쓰면 `_fo_summary` 를 지운다. `fo write` 는 리포트 `ticker` 가 입력 `meta.proxy`(KOSPI200) 와 같을 때만 받는다.
- `trader tool submit-fo-decision [--stage …]`: `FO_SUBMIT_RULE`(`_fo_decision*` 는 `_fo_inputs`·`_fo_debate_r0_bull`·`_fo_debate_r0_bear`·`_fo_summary` 가 있고 `_fo_analyst_*` 가 서로 다른 역할 ≥ 2). 아침 봉투는 `account_id·position_id·expected_version` 이 비어 있고 `request_id` 가 `_fo_inputs.meta.request_id` 와 같아야 한다. 재평가(`_fo_decision_r<n>_<계좌>`)는 봉투 네 값이 요청과 같고 포지션 `version` 이 지금도 `expected_version` 일 때만 받아 `reeval.apply` 로 넘긴다.
- 도구 서브프로세스는 파생 도구일 때만 브로커·시세·**캐시만의 마스터**(`fo_master.load(offline=True)`)·원장 국면·시장 뷰로 컨텍스트를 스스로 만든다.
- 교차 토론: Codex 토론 참여가 켜져 있고 한도가 되면 `_fo_debatex_r<n>_<bull|bear>`(2판, `fo bundle --lane b`)도 쓸 수 있고 요약에 `by_lane` 이 붙는다. 아니면 Claude 2인(`_fo_debate_r…` 만). 시간 연장·도중 실패 처리는 spot 과 같다.

## 체크포인트·control 키

체크포인트(`state/runs/<date>/KR/`): `_fo_inputs`·`_fo_analyst_<role>`·`_fo_debate_r<n>_<side>`·`_fo_summary`·`_fo_decision`(아침)·`_fo_decision_r<n>_<계좌>`·`_fo_reeval_r<n>_<계좌>`(재평가 요청 정본)·`_fo_risk_check_<계좌>[_r<n>]`·`_fo_order_<계좌>[_r<n>]`(진입, `executor._stage`)·`_fo_order_<계좌>_<intent>_<포지션id>`(`reduce` 기본 stage, intent 는 소문자).

control 네임스페이스(`Feature.control_namespaces`, A4): `fo_verified:<env>:<계좌>:<상품>:<수단>`(= `CONTRACT_VERSION`)·`fo_halted:<계좌>`·`fo_day_open:<계좌>`·`fo_alerts:<계좌>`·`fo_ref_iv:<포지션>`·`fo_pending_entry:<계좌>`·`fo_replace:<계좌>:<포지션>`. 대사 표지 `reconcile:<계좌>:KR:<날짜>` 는 커널 접두다. 어떤 자동 경로도 `fo_verified` 를 끄지 않는다.

## 모듈 규칙

(옛 `docs/architecture.md` 「선물옵션 방향성 레인」 이관. 괄호의 G/S/C 번호는 옛 리뷰 기록 항목이다.)

- **설정 `settings.py`**: `enabled`·`product`(mini|regular, `products` 표에 접두·승수·호가단위)·`vehicles`·`max_leverage`·`leverage_by_regime`(null = 미설정 → `max_leverage`, **0 = 그 국면 신규 금지**, `regime_cap()` 이 둘을 구분)·수명 손실 예산(`risk_per_trade_pct`)·스트레스 격자·`margin_rate`(공표 위탁증거금률 + asof — 상한이 아니라 정합성 검사용)·옵션 규칙·`reeval`·`session_cap` 등. `max_positions` 는 1 고정(상계·반대 보유 없음이 증거금 추론의 전제).
- **가격 모형 `pricing.py`**: Black-76(무위험이자 0)으로 시나리오 재평가(`reprice`). **IV 단위** — `FoQuote.iv`·`hist_vol` 은 KIS 응답 그대로 퍼센트(18.4)이고 `pricing.price` 는 소수(0.184)를 받으므로 변환은 `reprice` 한 곳(/100)에서만 한다(`test_pricing.py`).
- **검증 플래그**: `verify` 가 모든 단계가 성공했을 때만, 실제 보유 행으로 방향·종류·NAV 가 확인된 수단만 적는다. 게이트는 값이 현재 `CONTRACT_VERSION` 과 같을 때만 그 수단의 신규를 허용한다.
- **저장 계층 `store.py`**: 헬퍼는 commit 하지 않고 경계는 호출자(`with store.tx(conn)` = `BEGIN IMMEDIATE` … commit, 외부 호출(브로커)은 잠금 밖). `reserve` 가 한 트랜잭션에서 계좌 총량(포지션 수·계약 수·**살아 있는 다리 포함**·수명 예산)을 다시 세고 포지션 삽입/전환(`version+1`)과 `prepared` 다리 삽입을 함께 한다(실패면 아무것도 쓰지 않음). 축소 다리는 `active_reduce_leg_id` 가 비어 있을 때만. `transition(expected_version=)` 은 CAS 이고 상태 변경은 `types.TRANSITIONS` 표 안만. 수량 진행(`record_fill_progress`)과 경제값 진행(`apply_fill_economics`, 체결 동기화만)은 분리해 각 체결의 경제값이 정확히 1회 반영된다. 다리 실행 키는 `UNIQUE(position_id, intent, round, attempt)`.
- **멱등 `legstore.py`**: `LegStore(IdemStore)` — 키 `"leg:<id>"`, `claim`=`store.claim_leg`(CAS `prepared → sending`), `bind`=다리에 주문 연결, `release`=`cancel_prepared`. 현금 예약·승인 개념이 없어 `claim_reserved`·`has_approval` 은 Protocol 충족용이다.
- **상태기계 `positions.py`**: `entering → held|closed|unknown|resizing|closing`, `held → resizing|closing|unknown|awaiting_settlement`, `resizing → held|closing|closed|unknown|exit_failed`, `closing → closed|exit_failed|unknown|awaiting_settlement|held`, `exit_failed → closing|closed|unknown|held`, `awaiting_settlement → settled|unknown`, **`unknown → held|closed` 는 사람만**(`fo adopt`/`fo close`). 다리 상태 `prepared|sending|accepted|unknown|rejected|cancelled|filled|expired`. `recover`(첫 틱·재시작·야간 대조)는 sending/unknown 다리를 체결·주문 상태 조회로만 확정한다(`confirmed|unconfirmed|error` 삼상태 — 브로커 조회 실패는 `deferred` 로 다음 틱, 묶을 수 없는 것만 포지션 `unknown` + 알림, 전송 중 다리는 60초 유예·120초 넘은 prepared 는 조건부 취소). 브로커에만 있는 보유는 `unknown` 행으로 승계(자동 주문 대상 아님), `UnverifiedHolding` 은 보고만, 최종거래일 지난 보유가 잔고에서 사라지면 `awaiting_settlement`. `has_blocking_state` 가 unknown·exit_failed·미확정을 신규 금지 조건으로 돌려준다.
- **게이트 `rules.py`·`sizing.py`(순수 함수, 거부 사유 전부 수집)**: `evaluate(decision, port, cfg, quotes=, …) -> FoVerdict`. **먼저 진입/축소로 분기** — 축소 다리(`reduce_leg`)는 `enabled`·`vehicles`·`verified`·입력 신선도·대조·배수·`paused` 와 무관하다(paused 는 진입 사유 — OPEN·RESIZE↑ 와 HOLD 만 거부). 전송 여부(off·dry_run, 판단 유래의 paused)는 실행기가 정한다. 진입 거부 목록은 스펙 §4.2(enabled·수단·`verified`·`inputs_fresh`·진입 창 `in_entry_window`·만기·당일 손실/halted·킬스위치·`add_margin>0`·확신·손절 부호/거리·반대 보유/청산 중/unknown·`max_positions`·옵션 매도 `premium_stop_mult`·과거 asof). 대조 `cross_factor(regime, view_state, mode, direction, vehicle)` 는 스펙 §4.3 표 그대로. 배수 `L = min(target, max_leverage, regime_cap) × cross_factor`. **기존 포지션은 배수를 올릴 수 없고 `stop_pt`·`premium_stop`·`stop_fut` 는 유리한 쪽으로만**. 지정가는 호가 1단을 넘으면 보정하고 `LIMIT_DEV_PCT`(선물 2%·옵션 50%) 넘게 벗어나면 거부. 수명 손실 예산 = `nav_at_open × risk_per_trade_pct` − (실현 손실 + 비용 + 보유 계획 손실 + 미체결 계획 손실 + 이번 다리), 재충전 없음(`sizing.position_budget` 가 웹·LLM 과 같은 값을 준다).
- **실행기 `executor.py`**(주문은 여기서만, 다른 모듈은 `place_order`/`cancel_order` 를 부르지 않는다): `run_leg` 는 fo·paper 계좌만, 봉투 `position_id`/`expected_version` 검증("포지션 버전 불일치"), CLOSE/REPLACE → `reduce`, 그 외 시세·지수·잔고·주문가능 재조회(`fetch_fresh`) → `build_portfolio` → `rules.evaluate` 전체 재계산(이전 verdict 수량 재사용 없음) → 체크포인트 `_fo_risk_check_` → 기록만 경로(dry_run·paused·`mode≠auto` — `run_leg` 는 판단 경로라 CLOSE/REPLACE 도 `reduce(risk_exit=False)` 로 paused 면 기록만)는 `_fo_order_` 체크포인트만, 아니면 `store.reserve` → `_transmit`(`claim` 으로 `sending` 선점 → `place_order` → `accepted`/`rejected`/`unknown`). 접수·unknown 뒤 `orders` 행 기록 실패는 `OrderBookkeepingError`(다리는 `sending` 으로 남아 복구가 확정). **`sending`·`unknown` 다리는 절대 재전송하지 않고** `recover`/`continue_reduce` 가 조회로 확정한다. `reduce` 는 대기 진입 다리 회수 → 잔고 재조회 대조(원장과 다르면 **잔고로 덮어쓰지 않고** 오늘 체결을 이 포지션의 오늘 다리에 반영한 뒤 다시 대조, 그래도 다르면 `unknown` + 알림) → 축소 다리 1개(1차 지정가 호가 1단, `attempt > 1` 은 선물 시장가·옵션 호가 1단)를 낸다.
- **체결 동기화 `fills.py`**: 체결은 **원주문 계보**(`orgn_order_no` 또는 `order_no`) 단위 누적값으로 `fills` 행 1개를 교체한다. KIS `filled_amount` 는 통화 금액이라 승수로 나눠 포인트 합으로 쓰고(`fills.price` 는 포인트 단가, `orders.amount` 만 통화) 증분 평균가는 `Δ포인트 합 ÷ Δ수량` 으로만 계산한다. 다리 매칭은 같은 현지 날짜의 `order_no` → 없으면 주문번호 없는 `sending|unknown` 다리 중 같은 코드·방향·수량 후보가 정확히 1건일 때만 묶고(`orders(mode='recovered')`), 어디에도 붙지 않으면 `orders(asset='fo', mode='external')`. 계보 하나가 트랜잭션 하나(`insert_order/insert_fill(commit=False)`)이고 모순 계보는 그 포지션만 `unknown` + `[파생 체결 모순]` 알림으로 격리한다. 만료(`expire_legs`)는 조회 성공 ∧ 15:45 KST 뒤에만.
- **잔고·대조 `balance.py`**: `refresh`(`fo_nav_snapshot` 매번 + `fo_positions_snapshot`)·`reconcile`(`positions.recover` + unknown·미확정이면 `[대조 불일치]` 알림 1건 + `control.reconcile:<계좌>:KR:<날짜>`).
- **입력 `inputs.py`**: `build_inputs` 하나가 `_fo_inputs` 를 만든다 — `technical`(앞월물 선물 시세·일봉·분봉·호가·지표)·`sentiment`(069500 수급·미결제·풋콜비·체인 거래량)·`news_macro`(`ports["inputs"].recent` 시장 뉴스, 포트 없으면 이연 오류 — A8)·`context`(코스피200 지수·옵션 체인 ATM±4·상품·계좌 요약·국면·시장 뷰·현물 헤지 보유·비용·한도·검증 플래그) + `meta{request_id, asof, historical, max_input_age_s, futures_code}` + `errors`. 절마다 `{data, received_at, asof, error}`, 실패는 예외가 아니라 `errors`. `meta.historical`(`asof < now − max_input_age_s`)이면 세션은 dry-run 강제·도구는 기록만. `bundle` 이 서브에이전트 입력을 자른다(상한 24KB, 커널 `tooling.shrink` 가 evidence → 긴 dict 목록 꼬리 120/60/30건 → 문자열 순으로).
- **세션 `session.py`**: `run` — 파생 계좌 없음·오늘 `_fo_decision` 있음(멱등)·`enabled=false ∧ 보유 없음`·`session_cap` 도달이면 세션 없이 `skipped`. 아니면 입력 수집·저장 → `historical` 이면 dry_run 강제 → `SessionSpec(kind="fo")` → `_fo_decision` 읽기. 운영일은 `asof`(없으면 now)의 KST 날짜. 제출·재개 시점 신선도: `_fo_inputs.meta.asof` 가 `llm.timeout_s + max_input_age_s` 보다 오래면 `record_only: stale_submit`(G3-1). 재개 판정(`_fo_account_done`)은 보류 플래그·`_fo_order_<계좌>`·`fo_order_legs.request_id` 가 이 결정 봉투와 같은 다리를 근거로 본다(G3-2).
- **아침 제출 분기(`tools._fo_run_accounts`)**: 입력 날짜 불일치면 기록만, 계좌 하나의 예외는 그 계좌 `_fo_risk_check_<계좌>` 에 남기고 다음 계좌로(`OrderBookkeepingError` 는 CRITICAL + `[파생 주문 기록 실패]` 알림). 새 진입(OPEN, 보유 없는 RESIZE·REPLACE)은 진입 창 안이면 `run_leg`, 밖이면 `control.fo_pending_entry:<계좌>` 에 보류(엔진 ⑥ 이 진입 창에서 소비). 보유가 있는 RESIZE·REPLACE·HOLD·CLOSE 는 파생 장중이면 바로 `run_leg`. 같은 단계 재제출은 `already: true`. 재평가 요청은 같은 포지션·버전의 미결 요청을 재사용한다(`reused: true`).
- **모니터 엔진 `engine.tick(ctx, request_reeval=)`**: 계좌마다 ① `recover` → 진행 중 축소 `continue_reduce`·다리 없는 진입 `finalize`·정산 대기 `settled` ② 보호선 `exit_reason`(순수): 기초지수 `stop_pt`(지수 갱신 시간 ∧ 시장 시각 `max_quote_age_s` 안일 때만)·선물 `stop_fut`·옵션 **저장된** `premium_stop`, 만기 강제 청산(`triggers.force_close_day_of`), 익절, 보유기간 만료, `hold_overnight=false` 면 마감 `close_before_min` 전 `SESSION_CLOSE` ③ 유지 조건 `maintain`(노출 배수·추가증거금·주문가능현금 버퍼·수명 예산은 매 틱, 시나리오 스트레스는 15:00 확정 검사; 위반이면 옵션 매도 → 선물 → 옵션 매수 순 `RESIZE_DOWN`) ④ 일일 손실 한도 → `fo_halted` ⑤ 트리거 ⑥ 아침 보류 진입 ⑦ `balance.refresh`. 기록만 사유는 둘이다 — `hold`(진입·판단: ⑤ 트리거·재평가, ⑥ 보류 진입·REPLACE)와 `risk_hold`(`record_only(risk_exit=True)`: ① 정산 전이, ② 보호선, ③ 유지 축소, ④ halted). paused 는 `hold` 에만 들어간다. `risk_hold` 가 `dry_run`(`engine.NO_STATE_CHANGE`)일 때만 **정산 전이·halted 플래그를 쓰지 않는다**. 일시정지 중 위험 청산이 접수되면 계좌별 하루 1회 `fo_exit_while_paused` 알림(`executor._send_reduce`). off·approve·실전 미확인은 옛대로 상태를 기록한다(주문·취소만 내지 않음). 같은 날 같은 알림은 `_notify_once`.
- **트리거 `triggers.py`**: `candidates`(순수) → `check`(원장 `triggers`, kind `fo:<종류>`, ticker = 계약 코드)가 한 틱에 하나만(나머지 `detail.bundled`). 우선순위 invalidation(결정의 `invalidation_rules`, 커널 `execution.monitor_rules.rule_hit`) > move > take_profit 근접 > news > iv_spike > periodic > expiry. 처리 표지는 `실패:`(세션이 떴다 실패 — 하루 상한에 셈)·`보류:`(세션이 뜨지 않음 — 간격만)·`폐기:`(버림 — 간격만) 셋이며 상태는 `session_id` 접두로 읽는다.
- **재평가 `reeval.py`**: 엔진이 `triggers.record` → `request` → 큐 `fo_reevaluate` → `run`(`fo_resume` 세션 1회 → 처리 표지) → 제출 시 `apply`: **늦은 재평가 CAS**(`version == expected_version ∧ status == held` 아니면 `discarded`), 실행은 `run_leg` 하나, HOLD 는 손절 단조 + `tp_pt` 갱신, REPLACE·반대 방향 OPEN 은 청산을 먼저 내고 새 진입 봉투를 `control.fo_replace` 에 두며 `continue_replace` 가 보유 0 ∧ 살아 있는 다리 0 이 되면 운영일 1회 진입.
- **브로커 계층(커널 `broker/fo.py`·`fo_master.py`·`fo_codes.py`)**: 잔고는 연속조회 완주가 성공 조건이고 수량·방향·종류 세 검증을 모두 지나야 `FoPosition`(하나라도 실패면 `UnverifiedHolding` — 어떤 자동 주문의 대상도 아님). NAV 는 `prsm_dpast_amt` 가 비면 None(항등식 폴백 금지). `get_order_state` 는 원주문 계보에서 `filled/remaining/rejected` 를 필수 정수로 읽고 모순이면 예외. 응답 계약 버전 `CONTRACT_VERSION`(파서가 보는 필드·판정식이 바뀌면 올린다). 지표는 커널 `broker/indicators`(ML·spot·fo 공유), 장 시간은 `config/market_hours`(`is_fo_open` 08:45~15:45·최종거래일 15:20, `index_live`, `fo_close_hm`).
- **알림 `messages.py`**: 옛 문구 그대로 상수 5종·함수 15종. `reconcile_mismatch` 는 커널 것(`market="KR(파생)"`).
- **상태·텔레그램**: `/fo`(읽기 전용)와 `/status` 「파생(선물옵션)」 절은 `status.lines`(원장만) 하나를 쓴다.

## 웹 (M11, `routes.py`)

- `GET /fo/legs?account_id=&date=`·`GET /fo/summary`(파생 NAV 는 `fo_nav_snapshot` — accounts 통합 현황은 `account_nav` 만)·`GET /fo/today?date=`.
- `analytics_routes.py`: `GET /analytics/fo/scorecard`(만기 채점 결정의 방향 적중률·계약 손익 합, FLAT 제외)·`/analytics/fo/report?account_id=&days=`(파생 NAV 추이 — 레인이라 evaluation 을 import 하지 않고 `fo_nav_snapshot` 을 직접 읽는다)·`/analytics/fo/summary`(`/fo/summary` 와 같은 처리기).
- `system_routes.py`: `GET /system/fo`(옛 `/fo/account` 를 파생 계좌 전체로, T11-R1): 계좌마다 `id·env·mode·mode_note·paused·state·enabled` + 오늘 fo 트리거 + control 플래그 7종 요약(`fo_verified` 수단별 bool·`fo_halted` 오늘 여부·`fo_day_open` 기준 날짜·`fo_alerts` 오늘 보낸 수·`fo_ref_iv` 열린 포지션별 값·`fo_pending_entry` bool·`fo_replace` 포지션 id 목록), 시장·한도 맥락(`context`)은 하나. 계좌번호 없음.
- `RESOURCES`: `/accounts/{id}/positions` 파생 조각(파생 계좌만): `qty`=계약, `avg_price`=포인트(최근 `fo_positions_snapshot`), `krw_value`=승수 적용. stop 은 선물 `stop_fut`·옵션 `premium_stop`·지수 `stop_pt` 순 "손절 −N%"(평단 기준, 옵션의 지수 손절은 "손절 지수"), 손절이 없으면 강제 청산일까지 "만기 D-n"(level 없음). target 은 `tp_pt`("결정").
- `RESOURCES` 의 `/decisions`(그날 `fo_decisions`)·`/orders`(다리를 포지션·의도·라운드로 묶은 행, id = 첫 다리) 파생 조각, 상세 `GET /decisions/fo/{id}`·`/trace`.
- SSE `fo`(옛 지문), nav `accounts/fo-positions "파생 계좌"`·`today/fo-lane "파생"`.
- 웹 알려진 차이: 보유 행은 옛 `/fo/positions` 의 부가 필드(`current`·`eval`·`budget_*`·`live_legs`·`qty_target`·`leverage_used` 등)를 싣지 않는다(M11b 가 필요하면 더한다). `/analytics/fo/summary` 는 `/fo/summary` 의 별칭이다. 스펙 §2.5 의 `/fo/{scorecard,report}` 는 `/analytics/fo/*` 로, 옛 `/fo/account` 는 `/system/fo` 로 옮겼다.

## 알려진 차이 (옛 저장소 대비)

번호는 플랜 Ruling 과 1:1(`docs/superpowers/plans/2026-11-04-m10-fo.md`).

- A1 집행: 커널 `core.transmit` 대신 **자체 실행기**를 유지한다 — 다리 상태 갱신과 `orders` INSERT 가 한 트랜잭션이어야 하고, `OrderIntent.request()` 가 `FoOrderRequest` 와 호환되지 않으며, 알림이 fo 전용 15종이기 때문. 커널에서는 `OrderBookkeepingError`·`insert_order(asset="fo", commit=False)`·`notify_quietly`·`ExecPolicy`·`UNKNOWN_RESPONSE_CODE` 를 재사용. 스펙 §2.1 "전송은 커널 코어로" 와 다르다.
- A2 계좌·브로커: 커널 `AccountsPort` 에 `fo_accounts()`·`fo_brokers()` 추가. `accounts()`·`brokers()`·`market_client()` 는 현물 전용 그대로. `brokers()` 반환형은 list(Protocol 도 list 로 맞춤).
- A3 멱등: `LegStore(IdemStore)`(키 `leg:<id>`).
- A4 상태 저장소: 스펙 §2.4 의 `fo_flags` 테이블 대신 **커널 `control` 플래그**를 유지(옛 키 그대로).
- A5 잡 3개·큐 kind 4개: 전부 `gate="none"` 에 본문이 파생 시계를 직접 본다. `fo_session` 큐는 파생 장 시계를 보지 않는다(옛 `_auto_gate` 도 `fo_session` 제외 — 08:20 세션). 큐 kind `fo_reconcile` 은 플랜에 없었으나 `kr_fo_reconcile` 의 `catch_up="queue"` 대상이라 실행 중 추가했다.
- A6 세션 종류: `extra_cap_key=None`, 상한은 fo 쪽에서 세션 앞에 검사한다(커널 `LlmSettings` 에 기능 키를 넣지 않기 위해). 원자적 예약이 아니라 옛 `test_fo_caps_reserved_in_one_transaction` 보장은 없다 — 두 프로세스가 마지막 자리를 함께 잡을 수 있다(데몬 한 프로세스가 세션을 띄우므로 실제 위험은 낮음). 상한 초과는 `session.run` 이 `check_cap` 의 `PipelineError` 를 잡아 `skipped` 로 돌려준다(옛과 같음 — `trader fo session` 이 트레이스백 없이 JSON 한 줄·exit 0, M10 최종 리뷰).
- A7 도구: `trader tool fo …`·`submit-fo-decision` 이름·옵션 그대로. 제출 전제는 `StageRule`(`FO_SUBMIT_RULE`)로 표현.
- A8 판정 입력의 뉴스·거시: 옛 `news_macro` 는 regime 소유 시장 세션 입력을 불렀으나 레인은 regime 을 import 할 수 없다. `context.market_view`(state·stance·confidence·rationale)만 싣고 `news_macro` 절은 `{"data": None, "error": "M11: InputsPort.recent"}` — 아침 판정자가 뉴스·거시 원문 없이 판단한다(M9 A16 과 같은 결정). M12 T17: `ports["inputs"]` 가 있으면 `news_macro.data.news` 를 `recent`(asof 이전 24시간, ≤20건)로 채우고 오류를 비운다. 엔진 `_novel_news` 도 `recent(novel_only=True)` 로 세고, 포트가 없으면(테스트·CLI) ingest 소유 `news_items` raw SQL 로 폴백한다.
- A9 커널 승격(사전 Ruling 의 "사본 허용" 을 리뷰 후 철회): `rule_hit` → `kernel/execution/monitor_rules`, FAKE 헬퍼·`fake_report/fake_turn` → `kernel/session/fake`, 도구 검증·요약(`schema_error`·`validate_payload`·`summarize_turns`) → `kernel/session/tooling`, 지표 → `kernel/broker/indicators`(ingest 는 재노출), `get_trigger`/`mark_trigger_handled` → `kernel/execution/ledger`(spot 은 재노출). 남은 사본은 `_jsonable`·절 래퍼(`handled_marker` → `kernel/session/markers`, `boot_notifier` → `kernel/notify/boot` 로 M12 T14 승격).
- A10 다른 기능의 설정·테이블: `ctx.settings.risk`·`settings.hedge` 는 속성 접근만, `news_items` 는 raw SQL, `triggers`·`runs`·`orders`·`fills`·`control` 은 커널.
- A11 CLI 9개: 옛 이름·옵션·문구 그대로. 마스터는 **온라인으로 적재**한다(`fo_master.load` 에 `offline` 없음 — 옛 사용자 CLI 동작; 도구 서브프로세스만 `offline=True`).
- A12 텔레그램·상태: `/fo`·「파생(선물옵션)」 절. health `_fo` 4항은 M12, 웹 `/api/fo/*`·SSE `fo` 키는 M11.
- A13 메시지: 옛 문구 그대로.
- A14 `order_test.py`: 옛 그대로(실기 2 검증, 원장 미기록).
- A15 M9 이연 처리: F1 `skip_accounts` 의 `policy != "auto"` 추가는 **보존**(approve 모의 계좌는 의도된 승인 모드). F3 scalp adopt origin 필터·F4 monitor 게이트 부재 fail-closed·`judge_for` 락은 Task 1 에서 적용. 틱 단위 클라이언트 캐시는 M12, spot monitor 의 단타 보유분 제외는 M11 검토.
- A16 템플릿: `settings.yaml fo:` 블록, `schedule.yaml fo.kr`, `accounts.yaml` 에는 fo 계좌 **주석 한 줄**(기본은 현물만).
- A17 테스트 배치: `tests/features/fo/`(결합 헬퍼는 `_support.py`). 폐기: `test_stock_regression_fo`(M4 골든 회귀가 방어)·`web/test_fo_api`(M11)·`ops/test_health_fo`(M12). 표면 재작성: `test_cli`·`test_jobs`. `test_settings_denies_fo_session` 은 저장소에 `.claude/settings.json` 이 없어 이식하지 않았다(CLAUDE.md 금지 목록이 역할, deny 목록은 M12).
- A18 주입 시계: `positions.recover` 의 STALE_PREPARED 비교가 인자 `now` 를 쓴다(옛은 벽시계).
- A19 `FoProductCfg` 대신 커널 `broker.models.FoProduct`.
- A20 `in_entry_window(now, cfg)` 공개 이름(커널 `market_hours.in_window`). 진입 창 끝(15:30)은 **포함**이다(옛 동작 보존 — 플랜 문구 "배타" 와 1분 차이).
- A21 `test_option_quote_iv_is_percent_and_reprice_converts` → `tests/features/fo/test_pricing.py`.
- A22 `evaluation.nav_series(asset="fo")` 는 `fo_nav_snapshot` raw SQL 로 채웠다(stub 해소). `views.account_summary` 의존 2건은 새 저장소에 해당 함수가 없어 이식하지 않았다(M11).

실행 중 추가한 Ruling:

- `store.tx` 는 **자기 중첩만 합류**한다(모듈 전역 `_OPEN` 에 `id(conn)` 등록). 호출자가 남긴 암묵 tx 는 옛 동작대로 commit 한 뒤 `BEGIN IMMEDIATE` — 이식 코드 전체가 `with store.tx` 를 경계로 가정한다. `_transmit` 안의 `LegStore.bind` 는 합류해 원자성이 유지된다. `positions.recover` 는 진입부에서 `conn.in_transaction` 이면 commit 한다.
- 커널 `insert_fill(..., commit: bool = True)` 인자를 더했다(기본값 유지) — 체결 적재와 다리 갱신을 한 tx 로 묶기 위해.
- `extra_cap_key=None` + fo 쪽 상한: 상한 검사 위치가 커널 밖에 분산된다(A6).
- ~~paused 는 halted 플래그·정산 전이도 막는다~~ → 2026-11-09 대체: **일시정지는 위험 청산을 막지 않는다**. 위험 청산 = 엔진 자동 청산(손절 `stop_pt`·`stop_fut`·`premium_stop`, 만기 강제, 익절, 보유기간, `SESSION_CLOSE`, 유지 축소 RESIZE_DOWN/RISK_REDUCE)·`continue_reduce`·`recall_entries`·사람의 수동 청산(`fo_close`·`fo close`) — 호출자가 `executor.reduce(risk_exit=True)` 를 명시한다(기본은 fail-closed `False`). 판단 = `run_leg` 를 지나는 모든 것(아침 세션·재평가 CLOSE/REPLACE/RESIZE↓/HOLD, 보류 진입, REPLACE 두 번째 다리) — 일시정지 중 기록만. 정산 전이·halted 플래그는 위험 쪽이라 일시정지 중에도 쓰고(dry_run 만 막음), halted 면 회수 사유가 생겨 보류 진입 플래그를 지운다(M4 와 같은 규칙). `session_job`·`reconcile_job`·`reevaluate_request` 는 그대로 일시정지에 건너뛴다(대조는 주문이 없고 모니터 틱이 매번 체결 동기화·복구를 한다).
- `reevaluate_request` 는 사람 요청도 같은 게이트(`should_skip`·`is_fo_open`)를 지난다 — 장 밖 요청은 트리거를 `보류:` 로 닫는다.
- 엔진이 재평가를 큐에 넣을 때 `source="fo_monitor"` 를 쓴다(커널 `auto_gate` 가 paused 요청을 본문 전에 skip 해 트리거가 닫히지 않는 문제를 피함).
- 실전 파생 계좌 결과 모양: skip 결과에 `status: recorded`·`record_only`·`mode` 키를 더했다(옛과 다름).
- 실전 파생 계좌 2중 방어: `run_leg` 의 env 가드에 더해 `reduce`·`recall_entries`·`continue_reduce`(`real_guard` — 포트 정책이 auto 여도 `record_only: mode=off`)와 `_transmit` 진입부(예약 다리를 전송 전 취소, `REAL_FO_OFF_NOTE`)도 주문·취소를 내지 않는다(옛은 정책만 믿었다, M10 최종 리뷰).
- `/status` 「파생(선물옵션)」 절은 파생 계좌(원장 `accounts.asset='fo'`)도 fo_positions·fo_decisions 행도 없으면 생략한다(빈 목록).
- `engine._pending_entries` 는 회수 사유(halted·killswitch·cutoff·`enabled=false`)가 있으면 paused 중에도 보류 진입 플래그를 지운다(재개 뒤 결과와 동일, M4).
- `fo_session` 큐는 `is_fo_open` 을 보지 않는다(A5).
- 도구 서브프로세스만 오프라인 마스터를 쓰고, `fo_master.load` 는 CLI·데몬에서 온라인이다(A11).
- `FoExecContext.spot_accounts/spot_brokers` 는 inputs 가 읽지 않는다(헤지 보유는 포트로).

## 보존한 옛 결함

고치지 않고 옛 동작 그대로 둔 것(플랜 Global Constraints):

- `record_fill_progress` 가 CAS 없이 `version+1` 한다.
- `store.record_fill_progress` 의 `filled_amount` 인자는 쓰이지 않는다.
- `_flag_anomaly` 가 롤백 뒤 트랜잭션 밖에서 새 tx 를 연다.
- `run_leg` 는 `position_id` 가 None 이면 첫 열린 포지션을 암묵 선택한다.
- `fetch_fresh` 가 시세를 이중 조회할 수 있다(코드가 있으면 `quote_of` 를 두 번).
- `quote_of` 가 지수 절의 값을 시세 기초지수로 채운다(지수를 가격처럼 취급).
- `_ckpt` 가 체크포인트 쓰기 실패를 삼킨다.
- REPLACE·보류 진입 플래그를 실행 **전에** 지운다(실행이 죽으면 재시도 없음).
- 트리거 처리 상태가 `session_id` 문자열 접두(`실패:`/`폐기:`/`보류:`)에 의존한다.
- `rules.evaluate` 의 RESIZE 감액이 `sr.reasons` 를 무시한다.
- `HOLD` 의 `stop_pt 0.0` 은 falsy 로 취급된다.
- `_sentiment` 수급이 069500 proxy 다.
- 일일 손실이 밤사이 갭을 제외한 근사다(오늘 첫 관측 평가손익 대비 변화 + 당일 실현손익).
- 정산 손익은 추정하지 않는다(`pnl=None`).
- 추가 관찰(Task 5~7 보고): `fills._replace_fill` 이 `fills` 행을 DELETE 후 INSERT 해 `fills.id` 가 동기화마다 바뀐다 · `fills._external_order` 의 외부 주문 `price` 는 None · `_transmit` 은 bookkeeping 실패 시 `bookkeeping_failure` 알림 없이 CRITICAL 로그만 · `reduce` 의 `bal_failed` 스냅샷 상한 SQL 이 `MAX(date)` 를 계좌 전체로 잡는다 · `pnl_today(write=True)` 기준점은 paused 에서도 기록 · `_notify_once` 의 `fo_alerts` 는 기록만 모드에서도 쓴다 · `reeval.log` 미사용 로거 · `balance.__all__` 에 `"Decimal"`.

## 실기 미확인

옛 실기 1(2026-10-28 `fo verify`)에서 확정한 것(마스터 단축코드 체계, 호가 TR output2 로 시장 시각·1호가 채움, 기초지수 업종코드 2001, 주문가능 응답 필드, 계좌 전용 앱키 `KIS_PAPER_FO_*`)은 코드에 반영돼 있다. 보유 행이 있어야 확인되는 **실기 2 미확인**(확정 전 해당 수단은 `fo_verified` 가 없어 신규 거부):

- 잔고 output1 의 방향 필드(`sll_buy_dvsn_cd` 유무·값)와 `prdt_type_cd` 값(`broker/fo.PRDT_TYPE` 301/302/303 은 추정)
- `prsm_dpast_amt` 가 NAV 인지
- 체결 응답 `pdno` 가 표준 12자리인지 단축 코드인지
- 취소 행의 필드명(`rvse_cncl_dvsn_cd` 02 / `*_name` '취소')
- 미니옵션 승수 5만
- 옵션 매도 `ord_psbl_qty` 의 증거금 반영, `margin_rate` 의 보수성
- KIS 선물 `basis` 가 시장 베이시스인지
- `hts_rmnn_dynu` 잔존일 단위(달력/영업일)
- 만기 정산이 체결 조회에 실리는지(정산 손익은 알림에 추정하지 않음)
- 감액 다리 재시도 중 손절 우선(현재는 감액 완료 뒤 보호선, 1~3틱)
- 지수 시각 TR 은 미적용(`index_asof` None → 엔진 보호선은 선물 가격 조건만)

## 테스트

`tests/features/fo/` — 설정·스키마·타입·가격모형·메시지, 저장 계층·`LegStore`·상태기계·복구, 게이트·사이징·트리거, 실행기·체결·잔고·`test_lifecycle`(6조합 × 진입 → 보유 → 감액 → 손절 → 청산 + 재시작 복구 + 만기 정산), 입력·세션·도구·FAKE, 엔진·재평가·잡, CLI·order-test·상태·텔레그램, 교차 리뷰 보완(`test_review_s*`). 마이그레이션 14 는 `test_migrations.py`.

```
uv run pytest tests/features/fo -q -p no:cacheprovider
FAKE_CLAUDE=1 uv run trader fo session --dry-run   # fo 계좌 없으면 skipped JSON
```

세션은 `FAKE_CLAUDE=1` 일 때 `fake.run_fake_fo`(애널리스트 3 → r0 토론 → 요약 → FLAT HOLD, 재평가는 stdin 봉투 되돌림)가 같은 도구 함수로 흉내 낸다. `FAKE_CLAUDE=1` 없이 `trader fo session` 을, 그리고 `monitor|close|order-test` 를 테스트나 검증에서 돌리지 않는다.
