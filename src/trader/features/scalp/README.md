# scalp

## 책임

- 분 단위 단타 레인(tier=lane): 1분봉 신호는 파이썬 순수 함수가 감지·집행하고, 에이전트는 후보와 허용 여부(strategist `scalp_ok`)·진입 판정(`scalp-judge`)·보유 조언만 낸다.
- **모의 전용(paper only)·기본 끔**: `settings.yaml scalp.enabled: false`. 실전 계좌는 설정이 켜져 있어도 주문·기록 없이 건너뛴다.
- 설정: `settings.yaml scalp`(`ScalpCfg`, 17필드). 판정자는 `judge: false` 가 기본.
- 레인이라 다른 기능을 import 하지 않는다. 필요한 것은 포트(deps 주입)로 받는다.

## 포트

| 포트 | 용도 |
|---|---|
| `accounts` | 계좌 목록·모드 조회 |
| `regime` | 국면·킬스위치 조회 |
| `scan` | 후보(picks·뉴스·watch) 조회 |
| `stock_exec` | 주문 집행(executor 코어 경유) |

내놓는 포트: `scalp`(`ScalpPortImpl.open_tickers(state, market, today)` — 오늘 상태 파일의 `open` 종목 집합, 판정 대기 `pending` 제외, 읽기 실패면 빈 집합). spot 리밸런스가 이 종목을 건드리지 않는다.

## CLI·상태 절

- `trader scalp backtest --ticker … [--market KR|US --exchange NAS|NYS|AMS --days 1..5]` — 당일 1분봉 400개(`accounts` 포트의 시세 클라이언트, 실전 시세 키 필요)로 진입·청산 규칙을 재현해 JSON 1줄 `{ticker, market, bars, today, config, report}`. 설정 불변·주문 없음. `--days` 는 상태 파일이 있는 과거 운영일의 `report` 를 더한다.
- `trader scalp report [--market KR|US --days 1..60 --live]` — 상태 파일로 승률·사유별 집계 + 판정자 GO vs NO_GO 사후 비교(`judge`)와 `enabled`·`judge_enabled`. `--live` 면 오늘 NO_GO 의 가상 수익을 당일 분봉으로 잰다. 마감 기준은 `<시장>.monitor.end`(schedule.yaml 이 깨졌으면 정규장 마감).
- `/status` 「단타(모의 전용)」 절(`status.py`): 꺼짐 → `- 끔 (scalp.enabled=false)`, 켜짐 → 시장마다 `- {m}: 보유 {n}·청산 {k}[, 중단: …]`. 조회 예외는 절 안에서 한 줄(`- 단타 조회 실패: …`)로 바꾼다.


## 데몬 잡

| 잡 | kind | trigger | schedule_key | gate | 비고 |
|---|---|---|---|---|---|
| `{market}_scalp` | `scalp` | interval(기본 60s) | `{market}.monitor.interval_s`(spot `{market}_monitor` 와 같은 키) | none | start_soon 30s, catch_up none, teardown 판정자 풀 종료. 본문이 accounts 의 `monitor` 게이트(창·주말·휴장)와 일시정지를 직접 보고, 건너뛴 틱에도 그날 outbox 를 보낸다(T7-R1) |

단타 전용 스케줄 키는 없다(`schedule.yaml` 에 `scalp` 절을 더하지 않는다).

## 웹 (M11, `routes.py`)

- `RESOURCES`: `/accounts/{id}/positions` 단타 조각 — 모의 현물 계좌이면서 오늘 상태 파일 다리에 든 계좌만. stop/target = 체결 평단(없으면 신호가) × (1 ∓ `stop_pct`/`take_profit_pct`, `engine.pos_cfg` 덮어쓰기 반영), 출처 "단타 규칙". 설정에 단타 계좌 목록은 없다(모의 계좌 전체가 대상).
- SSE `scalp`: 시장별 오늘 상태 파일 mtime + 보유 수. nav `today/scalp-lane`.

## 알려진 차이 (옛 저장소 대비)

- A1 주문·취소·체결 동기화는 `ports["stock_exec"]`(spot 현물 집행 서비스 — 게이트·승인·멱등·기록은 spot `executor`, 취소는 커널 `core.cancel`, 체결은 spot `fills`)로만 지난다. 옛 엔진은 `orchestrator.executor` 를 직접 불렀다.
- A2 `scalp` origin 의 `OriginSpec` 은 spot `ORIGINS` 소유다(`47_scalp_<leg>`·`57_order_scalp_<leg>` 단계·`|q{qty}` 멱등 꼬리·`is_scalp` 면제). scalp `Feature.origins=()`.
- A3 상태는 JSON 파일 그대로(`state/scalp/<date>/<market>.json`·`events.jsonl`, 키 `open/closed/done/halted/pending/outbox/digest_at/carried`) — 원장 마이그레이션 없음. 다리(legs) 없는 옛 상태 파일을 읽는 호환 코드는 남겼지만, 그것만 검사하던 옛 legacy 테스트 1건(`test_engine_exit.py`)은 이식하지 않았다.
- A4·T7-R1 잡 `{market}_scalp` 은 게이트 `none` 이고 본문이 런타임에 `ctx.gates["monitor"]` 를 재사용한다(창 밖·주말·휴장). 일시정지는 본문이 따로 보고(monitor 게이트는 일시정지를 보지 않는다), 건너뛴 모든 경로에서 outbox 를 비운다. 틱 주기는 `{market}.monitor.interval_s`, 장중 창은 `<시장>.monitor.start/end` 를 재사용한다(단타 전용 주기 없음). `ScalpContext.exchanges` 는 빈 dict — 거래소는 후보 출처 → scan `analyze_targets` → 원장 `ticker_exchanges` 순으로 찾는다.
- A5 판정자는 커널 `quick.ask_json` 으로 묻고 `session_allowance(kind="scalp_judge")` 감속에 연동된다(옛엔 감속 연동 없음). 시간 제한은 `scalp.judge_timeout_s`.
- A6 후보 소스: 오늘 `_scan` picks + 새 뉴스 재분석 종목 + 관심종목(`ports["scan"].watchlist(conn, market)`).
- A7·T5-R1 동시 보유 상한(`max_positions`)은 `allowed()` 가 아니라 진입 루프가 open + pending 으로 센다(옛 동작 — `allowed()` 가 막으면 판정 대기가 버려진다).
- A8 거래 차단 사유(`block_reason`)는 커널 `broker.tradability` 로 승격된 것을 쓴다.
- A9 단타 보유는 커널 `ScalpPort.open_tickers` 로 내놓고 spot 리밸런스가 제외 집합에 합친다(옛 `_scalp_open`).
- A10 청산 알림 사유 표 `messages.SCALP_EXIT` 에 `carry_over: "이월 청산"` 을 더했다(옛 5개엔 없어 원문 키가 보였다 — 고친 것).
- A11 CLI `backtest|report` 의 옵션·출력 JSON 은 옛과 같다. 시세 클라이언트는 `accounts` 포트에서 받고, `--market` 이 KR·US 가 아니면 exit 1(옛은 US 로 취급). 옛 `backtest` 는 `today` 를 넘기지 않아 KR 00–09 KST 에 UTC 날짜가 되던 결함을 고쳤다(F2). `report --live` 의 US 거래소는 판정 사건의 값 → 원장 `ticker_exchanges` → `NAS`(옛은 `universe.us`).
- A12 사건 `at` 은 주입된 시계(`ctx.now`)다(옛은 `datetime.now()`).
- A13 `/status` 는 절별로 격리된다(커널) — 단타 절의 예외가 다른 절을 가리지 않는다. 단타 절은 자기 안에서도 예외를 한 줄로 바꾼다.
- A14 주문 알림 꼬리 표 `_kind` 는 `_entry`·`_exit` 다리 접미사를 벗겨 찾는다(커널 `execution.core`).
- A15 소수정(커널 `OPEN_STATUSES`·accounts `DEAD_STATUSES` 커널 import·`timeout_by_kind` `market: 600`·spot `RebalanceParams`)은 Task 1 에서 고쳤다. `market_view` 큐 kind `trading=False` 는 유지(regime README).
- T4-R1 `judge_context.regime` 은 `ports["regime"].load` 저장 레코드의 `state`·`count` 를 그대로 싣는다(옛 플랜의 `state()/core_count()` 아님) — 없으면 `{state: None, count: None}`.
- A16 판정 컨텍스트의 `news` 는 항상 `[]`(뉴스 포트 연결은 M11 `InputsPort.recent`)라, 판단 기준 "셋 중 둘" 이 사실상 거래량 + 호가 둘 다를 요구한다. 호가 조회에 실패하면 GO 를 낼 수 없다.
- 사건 키: `judge_drift` 의 현재가 키는 `now` → `price`(`event(now=)` 인자와 충돌). `judge` 사건은 옛과 같이 `go` 키.
- 오류 격리: 진입 루프의 종목별 try/except 가 모든 예외로 넓어졌다(`errors` 에 남김). `settle_pending` 은 격리 밖이다(옛 동일).
- `report()` 의 기본 `today` 는 UTC 날짜다(옛 동일 — CLI `report`·`backtest` 둘 다 `run_date_for` 를 넘긴다). `_monitor` 정규장 폴백은 `backtest`·`report` 둘 다 적용된다.
- `ScalpJudge._expire/discard` 의 `f.cancel()` 은 이미 실행 중인 future 를 멈추지 못해 만료된 판정도 일일 캡 1건을 쓴다(옛 동일).
- 판정자 md 의 `# 공통 규칙` 6줄은 괄호 앞 접두만 briefing 과 같다(괄호 보충은 scalp 스키마 용어) — 저장소 규약(`test_agents_contract.py`).
- 커널 `core.cancel` 은 잔량(`qty − SUM(fills.qty)`)으로 취소한다(T6-R1). 잔량 0 이면 브로커를 부르지 않고 `ok False`·"남은 수량 없음(전량 체결)".

## M10 에서 고친 것 (M9 F3·F4)

- F3 `adopt_entry_orders` SQL 이 `origin = 'scalp'` 로 한정한다 — 다른 origin 의 진입 주문을 단타 보유로 흡수하지 않는다.
- F4 `scalp_body` 는 `ctx.gates["monitor"]` 가 없으면 틱을 건너뛴다(`{"skipped": "monitor 게이트 없음"}`, fail-closed). 판정자 `judge_for` 의 단일체 생성은 `threading.Lock` 안에서 한다.
- F1(`skip_accounts` 에 `policy != "auto"` 추가)은 검토 후 **보존**했다 — approve 모의 계좌는 사용자가 의도한 승인 모드다. 틱 단위 브로커·시세 캐시는 M12.

## 보존한 옛 결함

고치지 않고 옛 동작 그대로 둔 것(플랜 Global Constraints):

- 판정자 `pending()` 은 죽은 코드라 이식하지 않았다(동작 차이 없음).
- `hedge.enabled` 가 꺼져 있으면 bear 국면 게이트·전략가 제안 게이트가 함께 무력하다.
- 마감 청산은 익절·손절 평가 뒤에 본다.
- `adopt_entry_orders` 는 계좌로 필터하지 않는다(origin 만 필터 — 위 F3).
- `scalp.enabled=false` 로 끄면 `scalp_body` 가 tick 전에 돌아가 열린 포지션의 청산·이월이 멈추고, 다음 날부터 `open_tickers` 에도 보이지 않아 리밸런스 제외에서 빠진다(옛 `_scalp_body` 동일). 끄기 전에 포지션을 비워야 한다.
- `approve` 모드 모의 계좌: `skip_accounts` 는 env·시장만 보고 모드는 보지 않으므로 단타 진입이 승인 요청으로 쌓이고, 사람이 승인하면 scalp 가 추적하지 않는 매수가 생긴다(옛 동일). 수정 후보 `skip_accounts` 에 `policy != "auto"` → M10 이연.
- `adopt_entry_orders`·`adopt_exit_orders` 는 `origin='scalp'` 로 거르지 않아 같은 계좌·종목의 결정 없는 다른 주문(rebalance·stop·hedge)을 흡수할 수 있다(플랜 "옛 SQL 그대로"). 원장 `origin` 열로 한 줄 필터 가능 → M10 이연.
- `trade_block`·`judge_context` 는 예외를 삼킨다(`trade_block` 은 차단 없음, `judge_context` 는 호가·국면·시장 판단 중 실패한 입력을 None 으로 두고 진행).
- `settle_pending` 은 `done` 에 먼저 넣고 `pending` 에서 나중에 지운다.
- 일일 손실 한도는 `closed` 의 실현분만 합산한다(열린 포지션 평가손 제외).
- 틱마다 후보 분봉을 순차로 부른다(병렬 없음).
- 판정자 클로저는 만들 때의 설정을 붙잡는다.
- 틱 전체가 `settle_pending` 예외에 중단된다.

## 테스트

`tests/features/scalp/` — 설정·스키마·기능 등록·메시지·상태 파일·신호·판정자·진입·청산·잡·포트·CLI(`test_cli.py`, 옛 `test_cli_regime.py::test_scalp_report_and_backtest_cli`)·상태 절(`test_status.py`). 픽스처는 `conftest.py`(합성 계좌 `paper-main`·`paper-two`·`real-1`, 합성 분봉 `bars()`).
