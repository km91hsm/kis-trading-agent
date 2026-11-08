# 조사: 일시정지(`paused`)가 막는 주문 경로 (2026-11-09, 기반 5ddc5e2)

줄 번호는 조사 시점 기준 — 구현 때 다시 확인한다.

## A. 게이트
- `features/accounts/gates.py:43-45` `build_market_gate`(check_paused=True) → `kernel/daemon/gates.py:18 should_skip`: `gate="market"` 잡(spot rebalance·reconcile·scan_analyze, regime, daily report)을 paused 면 skip. **변경 없음**(판단 매도·진입).
- `gates.py:48-51` `build_monitor_gate`(check_paused=False, Ruling 20): spot monitor·scan monitor 잡은 paused 에도 돈다.
- `kernel/daemon/queue_jobs.py:174-190 auto_gate`: 자동 출처 trading 큐 kind 를 market 게이트로 거른다. 변경 없음.

## B. 커널 실행 코어
- `kernel/execution/types.py:82-84` `ExecPolicy.transmits = mode!="off" and not paused and not dry_run` — 매수·매도 구분 없음.
- `spot/executor.py:978` 모든 계좌에 `ExecPolicy(paused=ctx.paused())`; `:1078`, `:1157`(전송 안 하면 주문·승인 요청 없음), `:1100` 「일시정지 중: 판정만 기록」. `:975 is_stop_loss = origin.origin == "stop"`. origin 값: stop/hedge/park/scalp/rebalance/reprice/기본.
- `kernel/execution/core.py:549-553 approve()`: paused 면 pending 유지 — **변경 없음**.
- `spot/executor.py:223-243 policy_pair` paused 콜백 없으면 fail-closed.

## C. 현물
- `spot/monitor.py:562-603 _sell_now`: paused → live=False(미체결 취소 안 함), `:589` `stop_paused` 알림(하루 1회 `sl_paused_notified:*`) 후 return 0. **손절 = 위험 청산 → 진행으로 변경.**
- `spot/monitor.py:140 _can_reevaluate`: paused → 재평가 세션 없음(익절·급변·무효화 트리거). **판단 → 그대로.**
- `spot/reprice.py:99`, `spot/rebalance.py:111`, `spot/hedge.py:433-443`, `spot/cash_park.py:205`: 판단 → 그대로.

## D. 단타
- `scalp/jobs.py:60` paused 면 틱 전체 skip(청산·이월 포함) → **제거**.
- `scalp/exits.py:188-196 can_trade` paused → 대기 → **paused 조건 제거**(dry_run·mode auto 조건 유지).
- `scalp/engine.py:99-102 allowed()` paused → 진입 막음 — **유지**. `scalp/switch.py:41` 진입 skip — 유지. `exit_skip_accounts` 는 이미 paused 무시.

## E. 파생
- `fo/jobs.py:93-103 _day_skip`(should_skip paused) — `monitor_job:193`(engine.tick 전체: stop_pt·stop_fut·premium_stop·만기 강제·TP·보유기간·SESSION_CLOSE·maintain RESIZE_DOWN·continue_reduce)는 **paused 로 skip 하지 않게**; `session_job:180`·`reconcile_job:217`·`reevaluate_request:265` 는 그대로.
- `fo/executor.py:342-352 _paused`/`record_only`(dry_run→paused→mode): `run_leg:703-704`(진입 또는 CLOSE/REPLACE→reduce), `recall_entries:1000`, `reduce:1140`, `continue_reduce:1317`. 축소·회수는 위험 청산.
- `fo/engine.py:73 NO_STATE_CHANGE=("dry_run","paused")`, `:95-97 _hold`, `:458`, `:494`(정산 전이), `:513`(트리거 보존), `:544`, `:555`, `:694`(진입 대기 보존).
- `fo/rules.py:222-223 evaluate`: paused → 「기록만」을 reduce 다리에도 적용 → reduce 는 제외.
- `fo/reeval.py:189` 재평가 HOLD TP 갱신 record_only — 판단, 그대로. `fo/jobs.py:281-300 close_request`(수동 fo_close) — executor.reduce 를 거침(사용자 수동 청산: 위험 청산 경로와 같이 진행해도 됨 — 빌더 판단, 보고).

## H. 바뀌어야 할 수 있는 기존 테스트
- spot: `test_monitor.py::test_paused_stop_loss_notifies_once`(:338), `::test_stop_loss_does_not_cancel_resting_sell_when_paused_or_off`(:672), `::test_paused_tick_records_trigger_notifies_once_and_places_no_order`(:864), `::test_reevaluation_blocked_when_paused_or_cap`(:240, 유지); `test_jobs.py::test_paused_daemon_monitor_tick_notifies_stop_paused_once`(:204); `test_executor.py::test_paused_blocks_orders_and_approval_requests`(:393), `::test_paused_blocks_approve`(:402), `::test_policy_without_paused_is_fail_closed`(:1037) — 유지.
- kernel: `tests/kernel/execution/test_core.py::test_approve_paused_or_mode_changed_keeps_pending`(:365, 유지), `test_types.py::test_policy_transmits_and_constants`(:30).
- 승인: `test_telegram.py::test_paused_keeps_pending`, `test_cli_approvals.py::test_cli_approvals_paused_keeps_pending`, `test_approvals_web.py` — 유지.
- rebalance·hedge paused 테스트 — 유지.
- scalp: `test_engine_exit.py::test_paused_does_not_drop_open_position`(:347), `test_jobs.py::test_scalp_body_skipped_when_disabled_or_outside_window`(:104).
- fo: `test_jobs.py::test_monitor_job_skips_without_accounts_or_outside_window`(:141), `::test_session_job_skips_holiday_and_paused`(:155, 유지), `::test_reevaluate_request_outside_fo_hours_releases_trigger`(:217), `::test_close_request_rechecks_state`(:243), `::test_paused_engine_consumes_nothing`(:300); `test_executor.py::test_paused_off_dry_run_no_cancel_no_order`(:314); `test_review_s4.py::test_paused_risk_reduce_does_not_lower_target`(:65), `::test_reeval_hold_tp_unchanged_when_paused`(:75, 유지); `test_review_s4_github.py::test_record_only_exit_keeps_pending_trigger`(:58), `::test_skipped_queued_reevaluation_releases_trigger`(:69); `test_review_s5.py::test_replace_flag_only_when_close_sent_and_discarded_after_stop`(:355); `test_rules.py::test_reduce_leg_ignores_enabled_vehicles_verified_freshness`(:317-318).
- daemon gates: `tests/kernel/daemon/test_gates.py` — 유지.

## I. 규칙 문장
- CLAUDE.md:64, `docs/user-guide.md`(:27, :119, :165), `docs/architecture.md:156`(Ruling 20), `accounts/gates.py:3-4`, `spot/monitor.py:6`, `scalp/engine.py:592-596`, `scalp/exits.py:189-190`, fo README :42/:47/:56/:89/:96/:145/:147/:151, `fo/executor.py:13`, `fo/engine.py:18`, `fo/rules.py:4-6`, scalp README :6/:32/:48, spot README :44/:54/:58.
