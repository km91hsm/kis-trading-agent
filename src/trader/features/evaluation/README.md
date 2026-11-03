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

(Task 14 에서 채운다)
