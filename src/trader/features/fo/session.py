"""파생 세션 실행(Phase 26 단계 3): 입력 수집·저장 → `kind="fo"` 오케스트레이터 세션 1회 →
`_fo_decision` 읽기. 데몬 잡 `kr_fo_session`·CLI `trader fo session` 이 같은 함수를 부른다. 세션이
결정·실행을 맡는 게 아니라 **도구**(`submit-fo-decision`)가 게이트·실행을 맡고 여기는 세션 앞뒤의
파이썬 몫만 둔다: 상한(`fo.session_cap`), 오늘 결정 멱등, `fo.enabled=false` ∧ 보유 없음이면 세션
생략(비용), 과거 asof(`meta.historical`)는 dry-run 강제.

M10(A6): 커널 `run_session` 은 `extra_cap_key` 를 `settings.llm.<키>` 로만 읽으므로 fo 상한은 커널이
모른다 — 여기서 세션을 띄우기 **전에** 오늘 `runs.kind='fo'` 세션 수(커널 `calls.sessions_today`,
FAKE 제외·실패 포함)가 상한 이상이면 커널 `PipelineError` 를 올린다. 파생 계좌·브로커·시세는
`ports["accounts"]`, 국면·시장 뷰는 `ports["regime"]`(`executor.context_sources`). 실행기 컨텍스트는
`LaneContext.ext["fo"]` 로 세션(FAKE)에 넘긴다."""

from __future__ import annotations

import logging
import sqlite3
from collections.abc import Callable, Mapping
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from trader.features.fo import inputs as inputs_mod
from trader.features.fo import store, switch
from trader.features.fo.executor import context_sources
from trader.features.fo.session_kinds import FO, agents_for_runner
from trader.features.fo.types import FoExecContext
from trader.kernel.lane.types import LaneContext
from trader.kernel.notify.telegram import NullNotifier
from trader.kernel.platform import paths
from trader.kernel.session import calls, run_store
from trader.kernel.session.run_store import read_checkpoint
from trader.kernel.session.runner import PipelineError, SessionSpec, run_session

log = logging.getLogger("trader.fo")

MARKET = inputs_mod.MARKET
DECISION_STAGE = "_fo_decision"
# 오케스트레이터 프롬프트의 런타임 체크포인트 이름(커널 `orchestrator_prompt(checkpoint_names=)`)
CHECKPOINTS = (
    "_fo_inputs",
    "_fo_analyst_<technical|sentiment|news_macro>",
    "_fo_debate_r<n>_<bull|bear>",
    "_fo_debatex_r<n>_<bull|bear>",
    "_fo_summary",
    "_fo_decision",
    "_fo_decision_r<n>_<account>",
)


def _decision(today: date, state: Path | None) -> dict | None:
    cp = read_checkpoint(run_store.market_dir(today, MARKET, state), DECISION_STAGE)
    data = cp.get("data") if isinstance(cp, dict) else None
    return data if isinstance(data, dict) else None


def check_cap(settings: Any, conn: sqlite3.Connection, now: datetime) -> None:
    """오늘 fo 세션 수(`runs.kind='fo'`, FAKE 제외·실패 포함) ≥ `fo.session_cap` 이면
    `PipelineError`(세션을 띄우지 않는다)."""
    cap = settings.fo.session_cap
    if calls.sessions_today(conn, now, kind=FO.runs_kind) >= cap:
        raise PipelineError(f"오늘 fo 세션 상한({cap}) 도달 — 세션을 띄우지 않습니다")


def run(
    settings: Any,
    conn: sqlite3.Connection,
    *,
    ports: Mapping[str, Any],
    master: list,
    date: date | None = None,
    now: Callable[[], datetime] = lambda: datetime.now(UTC),
    dry_run: bool = False,
    state: Path | None = None,
    session_runner: Callable[..., Any] | None = None,
    notifier: Any = None,
    cancel: Any | None = None,
    asof: datetime | None = None,
) -> dict:
    """{date, inputs_path, session, decision, skipped, dry_run, forced_dry_run}. `skipped` 가 있으면
    세션을 띄우지 않은 사유(오늘 결정 있음·계좌 없음·enabled=false∧보유 없음·세션 상한 — 상한은
    `check_cap` 의 `PipelineError` 를 잡아 사유로 돌려준다, A6). 운영일 `date` 는 생략하면
    `asof`(없으면 now)의 KST 날짜(`run_store.run_date_for`). `session_runner` 는 커널
    `run_session` 과 같은 서명(테스트 주입)."""
    at = asof or now()
    date = date or run_store.run_date_for(MARKET, at)
    out: dict[str, Any] = {
        "date": date.isoformat(), "inputs_path": None, "session": None, "decision": None,
        "skipped": None, "dry_run": dry_run, "forced_dry_run": False,
    }  # fmt: skip
    acc = ports["accounts"]
    accounts = [a for a in acc.fo_accounts() if a.asset == "fo"]
    if not accounts:
        out["skipped"] = "asset: fo 계좌 없음"
        return out
    existing = _decision(date, state)
    if existing is not None:
        out["decision"] = existing
        out["skipped"] = f"오늘 {DECISION_STAGE} 결정이 이미 있음(세션 생략)"
        return out
    if not switch.any_enabled(conn, settings, accounts) and not any(
        store.open_positions(conn, a.id) for a in accounts
    ):
        out["skipped"] = "파생 꺼짐(모든 계좌 스위치·fo.enabled) ∧ 보유 포지션 없음(세션 생략)"
        return out
    try:
        check_cap(settings, conn, now())
    except PipelineError as e:  # 커널 러너의 PipelineError 분기와 대칭(M10 최종 리뷰 I1)
        log.warning("파생 세션 생략: %s", e)
        out["skipped"] = str(e)
        return out
    ctx = FoExecContext(
        settings=settings, accounts=accounts, conn=conn, brokers=dict(acc.fo_brokers()),
        market_client=acc.market_client(), master=master, state=state, now=now,
        dry_run=dry_run, ports=ports,
        **({"notifier": notifier} if notifier is not None else {}),
        **context_sources(conn, now, ports),
    )  # fmt: skip
    inputs = inputs_mod.build_inputs(ctx, today=date, asof=at, accounts=accounts)
    historical = bool((inputs.get("meta") or {}).get("historical"))
    ctx.historical = historical
    if historical and not dry_run:
        log.warning(
            "파생 세션 asof %s 가 max_input_age_s(%s초)보다 과거 — dry_run 강제(주문 없음)",
            at.isoformat(), settings.fo.max_input_age_s,
        )  # fmt: skip
        dry_run = True
        out["forced_dry_run"] = True
        out["dry_run"] = True
        ctx.dry_run = True
    path = inputs_mod.save_inputs(inputs, today=date, state=state)
    out["inputs_path"] = str(path)
    if inputs.get("errors"):
        log.info("파생 입력 오류 절 %d건: %s", len(inputs["errors"]), sorted(inputs["errors"]))
    runner = session_runner or run_session
    spec = SessionSpec(market=MARKET, date=date, tickers=[], dry_run=dry_run, asof=at, kind="fo")
    lane = LaneContext(
        settings=settings, conn=conn, state=state if state is not None else paths.state_dir(),
        now=now, dry_run=dry_run, notifier=notifier if notifier is not None else NullNotifier(),
        ports=ports, ext={"fo": ctx},
    )  # fmt: skip
    try:
        res = runner(
            spec, FO, lane, agents=agents_for_runner(), cancel=cancel,
            checkpoint_names=CHECKPOINTS,
        )  # fmt: skip
    except PipelineError as e:
        log.warning("파생 세션 생략: %s", e)
        out["skipped"] = str(e)
        return out
    out["session"] = {
        "ok": res.ok, "fake": res.fake, "cost_usd": res.cost_usd, "error": res.error,
        "stages_written": res.stages_written,
    }  # fmt: skip
    out["decision"] = _decision(date, state)
    if not res.ok or out["decision"] is None:
        log.warning("파생 세션 실패 또는 결정 없음: %s", res.error)
        out["error"] = res.error or "결정 없음"
    return out
