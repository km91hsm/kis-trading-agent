"""트리거 재평가 (스펙 §5.2): trader 재호출 세션(kind=resume) → 31_decision_r<n> → 같은 게이트."""

from __future__ import annotations

import datetime as _dt
import logging
import threading
from collections.abc import Callable

from trader.features.spot import ledger
from trader.features.spot.schemas import CHECKPOINT_NAMES
from trader.features.spot.session_kinds import AGENTS, RESUME
from trader.kernel.lane.types import LaneContext
from trader.kernel.session.agents import by_name
from trader.kernel.session.runner import SessionResult, SessionSpec, run_session

logger = logging.getLogger("trader.spot")


def reevaluate(
    lane_ctx: LaneContext,
    *,
    market: str,
    date: _dt.date,
    ticker: str,
    exchange: str | None,
    trigger_id: int,
    trigger: dict,
    session_runner: Callable[..., SessionResult] | None = None,
    cancel: threading.Event | None = None,
) -> SessionResult:
    """재평가 세션 1회. 결정 제출·실행은 세션 안의 `tool submit-decision --stage` 가 한다.
    cancel(threading.Event)이 서면 세션 프로세스 그룹을 끊는다 — 데몬 종료 때 고아 세션이 주문을
    계속 내지 않게(전체 검토 P1)."""
    spec = SessionSpec(
        market=market,
        date=date,
        tickers=[{"ticker": ticker, "exchange": exchange}],
        dry_run=lane_ctx.dry_run,
        asof=lane_ctx.now(),
        kind=RESUME.kind,
        extra={"trigger": trigger},
    )
    runner = session_runner or run_session
    res = runner(
        spec,
        RESUME,
        lane_ctx,
        agents=by_name(AGENTS),
        checkpoint_names=CHECKPOINT_NAMES,
        **({"cancel": cancel} if cancel is not None else {}),
    )
    if trigger_id:
        ledger.mark_trigger_handled(
            lane_ctx.conn, trigger_id, handled_marker(res), handled_at=lane_ctx.now().isoformat()
        )
    if not res.ok:
        logger.warning("재평가 세션 실패 %s %s: %s", market, ticker, res.error)
    return res


def handled_marker(res: SessionResult) -> str | None:
    """triggers.session_id 에 남길 값. 세션이 떴어도 실패(타임아웃·예산·오류)면 '실패:' 접두사를
    붙여 운영일 1회 제한(`reevaluated_between`)에서 빼고 30분 뒤 재시도할 수 있게 한다."""
    if res.ok:
        return res.session_id
    return f"실패: {res.error or 'unknown'} ({res.session_id or '-'})"[:200]
