# evaluation

## 책임

- 원장 v13: `memory_entries`(결정별 메모리·결과·반성)·`calibration_runs`·`shadow_portfolios`·`rl_shadow`.
- `memory/vector.py`(12차원 상황 벡터·코사인)·`memory/store.py`(3층 반감기 검색·승격·정리·서킷브레이커).
- 스키마: `schemas.py`(`Reflection`·`CalibrationReport` 등).
- 제어 플래그: `memory_breaker[:<market>]`, `vix_last`.

## 설정

- `settings.yaml` `memory`(`half_lives`·`warmup`·`drawdown_stop_pct`·`vix_spike_pct`·`top_k`).
- `schedule.yaml` `reflect`·`daily_report`·`paper_review`·`rl_train`.

## 알려진 차이

- A3: `memory_entries` 에 `realized_return`·`alpha_vs_benchmark` 컬럼이 없다(스펙 §2.4). 성숙은 `fill_outcome` 한 가지 시그니처.
- A1/A2: RL 사이저는 evaluation `rl/`(ml→evaluation 순환 방지), 레인 접점은 `ports["evaluation"]`(`EvaluationPort`) 하나. 옛 `ml/rl` 과 `trader rl` 은 없다.
- RL pickle 클래스 경로는 `trader.features.evaluation.rl.policy.LinearPolicy` 다. 옛 `state/models/rl_sizer/*` 는 로드되지 않으니 `trader evaluation rl train` 으로 다시 학습한다. 승격·강등(`rl promote|demote`)은 사용자만.
- A5: `round_trip_bp` 는 커널 `execution/costs.py` 로 승격(M7 `ROUND_TRIP_BP` 중복 해소). A6: `MonitorContext.close_table` 주입이 살아 있다.
- A7: `features_snapshot` 06:30 cron 을 유지하고 reflect 도 다시 스냅샷한다(중복 스냅샷). A8: `retention.prune` 은 reflect 에 없다(M12), fills 선동기화 없음.
- A10: `usage_probe` 는 커널 내장 잡이며 간격이 1800s 고정이다(설정 `llm.usage_probe.interval_min` 이 남아 있어도 읽지 않는다 — 바꿔도 효과 없음, 반영은 M12). A13: CLI 는 `trader evaluation …` 만(최상위 별칭은 M12). A15: `test_performance_fo` 6건과 fo `nav_series` 는 M10 에서 해소 — `nav_series(asset="fo")` 가 `fo_nav_snapshot`(NAV 있는 행, 마지막 날짜 기준 `days`, 평일만)을 raw SQL 로 읽는다(레인 테이블 읽기는 허용, import 아님). `views.account_summary` 의존 2건은 이식하지 않았다(M11).
- `quick.ask_json` 은 `agents.with_schema` 를 재사용한다(T2-R1; md 가 스키마를 이미 품으면 덧붙이지 않는다). `readiness.interval_kinds_of` 는 interval 잡의 `kind` 를 모은다(T9-R2, T9-R1 을 뒤집음; 데몬은 `runs.kind=job.kind` 로 쓰고 `runs_kind="monitor"` 세션은 없다). 대상은 discover 된 기능 + 커널 `QUEUE_FEATURE`·`BUILTIN_FEATURE`.
- `store.held_at` 은 `account_nav` 만 있는 날을 "보유 없음"으로 본다. 집행 브레이커는 계좌 자신의 30일 NAV 를 쓴다(옛 paper 합산 아님; paper 계좌가 둘 이상이면 마지막 것이 이긴다).
- `convergence_rate` 는 `debate_turns.decision_date`, `signal_hits`/`pending` 은 `analyst_reports.decision_date` 와 조인한다. `open_window._exchanges` 는 spot 소유 `ticker_exchanges` 테이블을 SQL 로 읽는다.
- `ports._decided_at` 은 US 백필(운영일이 지금이 아닐 때)에도 `MARKET_CLOSE["KR"]`(15:30 KST)를 쓴다 — 플랜 Task 5 결정(Ruling F2).
- 미국 일일 리포트(16:20 ET)는 07:10 KST `fills_sync` 보다 먼저 돌아 `realized_5d` 가 하루 늦을 수 있다.
- 이연: M11 `/api/performance`·`/api/rl`·`/api/memory`·`lots` CSV·`metrics.pending_approvals/account_stance/stance_by_market`; M12 status/health 보정 재점검.
- `theme.mature`/`mature_views` 는 reflect 가 부른다(M6 이연 회수).

### 보존한 옛 결함 (고치지 않음)

- `fill_outcome` 를 이미 채점된 항목에 다시 부르면 재승격(재채점)된다 — 옛 동작 그대로. 같은 항목에 반복 호출하지 않는다.
- `cash_effect` 는 `x.partial` 을 보지 않는다(`y.partial = 0` 만 거른다) — 옛 동작. `partial` 은 유예(14일) 뒤에도 종가가 없어 남은 비중으로 재정규화한 행 표시로 체결과는 관계없고, x 는 `weights_json='{}'`(현금일)이라 `x.partial` 은 항상 0이므로 실질 효과는 없다.
- `calibration_runs` 는 기록만 하고 읽는 곳이 없다(write-only) — 이력 확인은 DB 를 직접 조회한다.
- `_paper_days` 는 창이 무제한이다(전 기간 페이퍼 일수) — 오래된 계정은 readiness 일수 기준이 쉽게 충족된다.
- `build_calibration` 의 `calibration_runs` KST 하루 1행 upsert 는 마지막 행(`ORDER BY id DESC LIMIT 1`)만 비교한다 — 시계가 거꾸로 가거나 행이 수동으로 끼어들면 같은 날 2행이 생긴다(CSV 입력은 없다).
- `store.matured_count` 는 시장 구분 없이 센다 — US 워밍업이 KR 만기 엔트리로 채워질 수 있다(옛 동작, Ruling F1).

### 이 플랜이 고치거나 바꾼 것

- A4: `shadow_portfolios.def_version` 은 NOT NULL 이라 옛 legacy NULL 행 테스트 절반은 이식하지 않았다(`record` 가 `DEF_VERSION` 을 쓰는 단언만).
- A11: `readiness.check` 는 interval 잡 종류를 하드코딩하지 않고 `interval_kinds_of(features)` 로 받는다(옛 `INTERVAL_KIND_SQL` 대체). 보정 설정은 `calibration` 인자로 받는다.
- A12: `memory_context` 의 `latest_row` 는 `asof=당일` 로 통일(옛은 asof 없이 최신 행이라 기록·질의 벡터 피처 날짜가 달랐다). `ReflectParams.market` 이 주어지면 그 시장만 복기한다(옛은 무시).
- A14: `metrics.decision_distribution`·`convergence_rate` 는 evaluation `metrics.py` 소유(`kernel.session.debate.CONVERGENCE_GAP` 사용). 옛 `metrics.pending_approvals`·`account_stance`·`stance_by_market` 는 M11 웹에서.
