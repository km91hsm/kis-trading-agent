"""regime 데몬 잡: `{market}_regime`(국면 판정 → 시장 세션 → 전략가 지표 재판정 → 제안 채점)·
`ksd_events`(예탁원 권리 일정 수집)와 큐 kind `market_view`(시장 세션 1회).

옛 데몬 `_regime_body`·`_market_view_body`·`_regime_apply_view`·`_mature_market_views`·`_ksd_body`.
시세·계좌·브로커는 `deps.ports["accounts"]` 로 받고, 시장 세션의 `LaneContext.state` 는
`deps.state`(= `paths.state_dir()`, 도구가 쓰는 곳과 같은 state)다."""

from __future__ import annotations

import logging
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

from trader.features.regime import blackout, inputs, messages
from trader.features.regime import market_view as mv
from trader.features.regime import regime as regime_mod
from trader.kernel.config import KST
from trader.kernel.lane.types import LaneContext
from trader.kernel.notify import messages as kmessages
from trader.kernel.notify.telegram import NullNotifier
from trader.kernel.registry.types import JobSpec, QueueKind
from trader.kernel.session import calls
from trader.kernel.session.run_store import run_date_for

logger = logging.getLogger("trader.regime")


def _notifier(deps) -> Any:
    return deps.notifier or NullNotifier()


def _port(deps) -> Any | None:
    return (deps.ports or {}).get("accounts")


def _market_client(deps) -> Any | None:
    port = _port(deps)
    return port.market_client() if port is not None else None


def _lane(deps, conn) -> LaneContext:
    """시장 세션 문맥. state 는 `deps.state` — 도구(`regime.tools.tool_ctx`)와 같은 곳을 읽고 쓴다.
    거시 키는 `ext["secrets"]` 로 넘긴다(`market_view.build_inputs`)."""
    return LaneContext(
        settings=deps.settings,
        conn=conn,
        state=deps.state,
        now=deps.now,
        dry_run=True,
        notifier=_notifier(deps),
        ports=deps.ports,
        ext={"secrets": deps.secrets},
    )


def regime_update(deps, market: str) -> dict:
    """국면 판정 갱신(운영일 1회). 실패하면 직전 상태 유지 + 경고, 전환이면 알림 1건."""
    cfg = deps.settings.hedge
    if not cfg.enabled:
        return {"skipped": "hedge.enabled=false"}
    try:
        mc = _market_client(deps)
        closes = [c.close for c in inputs.proxy_candles(mc, market, cfg, now=deps.now())]
        if not closes:
            return {"error": "지수 proxy 일봉 없음"}
        conn = deps.conn_factory()
        try:
            out = regime_mod.update(
                conn,
                market,
                closes=closes,
                cfg=cfg,
                today=run_date_for(market, deps.now()),
                vix=inputs.vix(deps.secrets, market),
                now=deps.now(),
                flow_weak=inputs.flow_weak_signal(mc, market, cfg),
            )
        finally:
            conn.close()
    except Exception as e:  # noqa: BLE001 - 국면 갱신 실패가 다음 단계를 막지 않는다
        logger.exception("국면 판정 실패 %s", market)
        _notifier(deps).send(
            kmessages.event(
                "국면 판정 실패", f"{market}: {type(e).__name__}: {e} — 직전 상태를 유지해요"
            )
        )
        return {"error": f"{type(e).__name__}: {e}"}
    if out.get("state") != out.get("prev_state"):
        _notifier(deps).send(
            messages.regime_change(
                market=market,
                before=out.get("prev_state"),
                after=out["state"],
                count=regime_mod.count_text(out),
            )
        )
    return out


def market_view_body(deps, market: str) -> dict:
    """시장 세션(Phase 18): 국면 갱신 뒤 운영일 1회. 거시 애널리스트 → 토론 → 전략가 제안.
    실패·상한이면 제안 없이(규칙 기본값) 간다. 알림은 제안이 왔을 때만 1건(실패는 실패 알림)."""
    cfg = deps.settings.hedge
    if not cfg.enabled or not cfg.market_session:
        return {"skipped": "hedge.market_session=false"}
    if deps.stop_event.is_set():
        return {"skipped": "종료 중"}
    now = deps.now()
    today = run_date_for(market, now)
    conn = deps.conn_factory()
    try:
        if mv.load_view(conn, market, today) is not None:
            return {"skipped": "오늘 이미 제안 있음"}
        llm = deps.settings.llm
        cap_hit = (
            calls.sessions_today(conn, now) >= llm.daily_session_cap
            or mv.market_sessions_today(conn, now) >= llm.market_session_cap
        )
        port = _port(deps)
        accounts: list = list(port.accounts()) if port is not None else []
        brokers = {b.account.id: b for b in (port.brokers() if port is not None else [])}
        ctx = _lane(deps, conn)
        built = mv.build_inputs(
            ctx,
            market=market,
            today=today,
            asof=now,
            market_client=_market_client(deps),
            brokers=brokers,
            accounts=accounts,
        )
        out = mv.run_market_view(
            ctx, market=market, date=today, inputs=built, cap_hit=cap_hit, cancel=deps.stop_event
        )
    finally:
        conn.close()
    view = out.get("view")
    if view:
        _notifier(deps).send(
            messages.market_view(
                market=market,
                stance=view.get("stance"),
                hedge_weight=view.get("hedge_weight"),
                leveraged=view.get("leveraged", True),
                rationale=view.get("rationale"),
            )
        )
    elif out.get("error"):
        _notifier(deps).send(
            kmessages.event(
                "시장 세션 실패", f"{market}: {out['error']} — 규칙 기본값으로 진행해요"
            )
        )
    return out


def apply_view(deps, market: str) -> dict:
    """시장 세션 뒤: 오늘 제안이 RISK_OFF(확신 ≥ hedge.agent_min_confidence)면 전략가 신호를 4번째
    지표로 켜고 국면을 다시 판정한다(같은 운영일 재판정, streak 겹침 없음). 전환이면 알림 1건.
    RISK_OFF 가 아니면 신호를 끈 채 재판정(어제 켜진 신호가 남지 않게)."""
    cfg = deps.settings.hedge
    if not cfg.enabled or not cfg.agent_signal:
        return {"skipped": "hedge.agent_signal=false"}
    today = run_date_for(market, deps.now())
    conn = deps.conn_factory()
    try:
        view = mv.load_view(conn, market, today)
        on = bool(
            view
            and view.get("stance") == "RISK_OFF"
            and float(view.get("confidence") or 0) >= cfg.agent_min_confidence
        )
        before = (regime_mod.load(conn, market) or {}).get("state")
        out = regime_mod.reapply(
            conn, market, agent_risk_off=on, cfg=cfg, today=today, now=deps.now()
        )
    finally:
        conn.close()
    if out is None:
        return {"skipped": "오늘 국면 판정 없음"}
    if out.get("state") != before:
        _notifier(deps).send(
            messages.regime_change(
                market=market, before=before, after=out["state"], count=regime_mod.count_text(out)
            )
        )
    return {**out, "agent_risk_off": on}


def mature_views(deps, conn) -> int:
    """만기 지난 시장 제안에 지수 proxy 수익·적중을 채운다(실패는 경고만)."""
    cfg = deps.settings.hedge
    mc = _market_client(deps)
    n = 0
    for market in deps.settings.markets:
        try:
            candles = inputs.proxy_candles(mc, market, cfg, now=deps.now(), days=120)
            n += mv.mature_views(
                conn,
                market,
                today=run_date_for(market, deps.now()),
                candles=[(c.date, c.close) for c in candles],
            )
        except Exception as e:  # noqa: BLE001 - 채점 실패가 다른 시장을 막지 않는다
            logger.warning("시장 제안 채점 실패 %s: %s", market, e)
    return n


def regime_job(deps, job) -> dict:
    """`{market}_regime` 크론 본문: 국면 갱신 → 시장 세션(`hedge.market_session`) → 전략가 지표
    재판정 → 제안 채점. 시장 세션·재판정 예외는 격리(국면 갱신 결과는 그대로 남는다)."""
    market = job.market
    cfg = deps.settings.hedge
    out: dict[str, Any] = {"regime": regime_update(deps, market)}
    if out["regime"].get("error"):
        out["error"] = out["regime"]["error"]
    if cfg.market_session:
        try:
            out["view"] = market_view_body(deps, market)
        except Exception as e:  # noqa: BLE001 - 시장 세션 실패가 국면·재판정을 막지 않는다
            logger.exception("시장 세션 실패 %s", market)
            _notifier(deps).send(kmessages.failure("시장 세션 실패", market, e))
            out["view"] = {"error": f"{type(e).__name__}: {e}"}
    try:
        out["apply"] = apply_view(deps, market)
    except Exception as e:  # noqa: BLE001
        logger.exception("전략가 지표 반영 실패 %s", market)
        _notifier(deps).send(kmessages.failure("국면 재판정 실패", market, e))
        out["apply"] = {"error": f"{type(e).__name__}: {e}"}
    conn = deps.conn_factory()
    try:
        out["matured"] = mature_views(deps, conn)
    finally:
        conn.close()
    return out


def ksd_body(deps, job) -> dict:
    """예탁원 권리 일정 수집(평일 07:05 KST) → control `ksd_events:KR`. 시세 없으면 생략.
    KR 시장이면 블랙아웃이 꺼져 있어도 모아 둔다(켜는 날 바로 쓰게). stale 알림만 켜져 있을 때."""
    mc = _market_client(deps)
    if mc is None or not hasattr(mc, "ksd_schedule"):
        return {"skipped": "시세 클라이언트 없음"}
    cfg = deps.settings.event_blackout
    r = blackout.collect(mc, cfg, today=deps.now().astimezone(KST).date(), now=deps.now())
    conn = deps.conn_factory()
    try:
        data = blackout.store(conn, r)  # 실패한 종류는 지난 일정 유지(2-O9)
    finally:
        conn.close()
    stale = blackout.stale_kinds(data, cfg, now=deps.now())
    if stale:
        logger.warning("권리 일정 수집 오래됨: %s", stale)
        if cfg.enabled:  # 켜져 있을 때만 알림 — 꺼진 기능의 실패로 매일 알리지 않는다
            _notifier(deps).send(messages.ksd_stale(stale))
    return {"events": len(data["events"]), "errors": r["errors"], "stale": sorted(stale)}


class MarketParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    market: Literal["KR", "US"]


def market_view_request(deps, params: dict, progress) -> dict:
    """큐 `market_view` 본문: 시장 세션 1회(`market_view_body`)."""
    return market_view_body(deps, params["market"])


# heavy: 시장 세션(LLM)이 다른 무거운 잡·큐 세션과 겹치지 않게(옛 scan_analyze heavy 락)
REGIME = JobSpec(
    id="{market}_regime", kind="regime", trigger="cron", per_market=True,
    schedule_key="regime.{market}", interval_s=None, enabled=lambda s: s.hedge.enabled,
    gate="market", heavy=True, misfire="idempotent", catch_up="direct", start_soon_s=0,
    body=regime_job, days="mon-fri",
)  # fmt: skip

KSD_EVENTS = JobSpec(
    id="ksd_events", kind="ksd_events", trigger="cron", per_market=False,
    schedule_key="ksd_events", interval_s=None, enabled=lambda s: "KR" in s.markets,
    gate="none", heavy=False, misfire="idempotent", catch_up="direct", start_soon_s=0,
    body=ksd_body, days="mon-fri",
)  # fmt: skip

QUEUE_KINDS = (
    QueueKind(
        "market_view", MarketParams, "시장 세션", "slow", 0, False, False, market_view_request
    ),
)
