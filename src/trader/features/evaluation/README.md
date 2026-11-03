# evaluation

초안 — Task 14 에서 마무리한다.

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
- A10: `usage_probe` 는 커널 내장 잡이며 간격이 1800s 고정이다(`llm.usage_probe.interval_min` 미사용). A13: CLI 는 `trader evaluation …` 만(최상위 별칭은 M12). A15: `test_performance_fo` 6건과 fo `nav_series` 는 stub, M10.
- `quick.ask_json` 은 `agents.with_schema` 를 재사용한다(T2-R1; md 가 스키마를 이미 품으면 덧붙이지 않는다). `readiness.interval_kinds_of` 는 확장된 잡 id 만 모은다(T9-R1; kind 를 넣으면 `monitor` 세션 run 이 오류율 분모에서 빠진다).
- `store.held_at` 은 `account_nav` 만 있는 날을 "보유 없음"으로 본다. 집행 브레이커는 계좌 자신의 30일 NAV 를 쓴다(옛 paper 합산 아님; paper 계좌가 둘 이상이면 마지막 것이 이긴다).
- `convergence_rate` 는 `debate_turns.decision_date`, `signal_hits`/`pending` 은 `analyst_reports.decision_date` 와 조인한다. `open_window._exchanges` 는 spot 소유 `ticker_exchanges` 테이블을 SQL 로 읽는다.
- 미국 일일 리포트(16:20 ET)는 07:10 KST `fills_sync` 보다 먼저 돌아 `realized_5d` 가 하루 늦을 수 있다.
- 이연: M11 `/api/performance`·`/api/rl`·`/api/memory`·`lots` CSV·`metrics.pending_approvals/account_stance/stance_by_market`; M12 status/health 보정 재점검.
- `theme.mature`/`mature_views` 는 reflect 가 부른다(M6 이연 회수).
