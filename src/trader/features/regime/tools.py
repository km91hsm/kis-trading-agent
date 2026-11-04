"""시장 세션 도구 구현 (Phase 18): `trader tool market bundle|write|summarize`·
`submit-market-view`. 시장 단위 체크포인트 `_market_*` 를 `state/runs/<날짜>/<시장>/` 에 둔다.

모든 함수는 예외 대신 {"ok": bool, ...} dict 를 돌려준다(CLI 가 JSON 1줄로 그대로 출력).
순서 강제는 프롬프트가 아니라 여기의 전제조건이 맡는다:
  market write ← `_market_inputs`(파이썬이 세션 앞에서 만든다),
  submit-market-view ← `_market_analyst` 와 `_market_summary`."""

from __future__ import annotations

import logging
import re
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from trader.features.regime.schemas import AnalystReport, DebateTurn, HedgeProposal
from trader.kernel.ledger import control
from trader.kernel.platform import paths
from trader.kernel.session import tooling
from trader.kernel.session.run_store import checkpoint_payload as _wrap
from trader.kernel.session.run_store import market_dir, read_checkpoint, write_checkpoint

logger = logging.getLogger("trader.regime")

BUNDLE_LIMIT_BYTES = 24_000  # Claude Code Bash 출력 한도(30KB, UTF-8 바이트) 아래
MARKET_PROXY_LABEL = {"KR": "KOSPI200", "US": "SPX"}
MARKET_VIEW_FLAG = "market_view:"
_MARKET_DEBATE_RE = re.compile(r"^_market_debate_r(\d+)_(bull|bear)$")
_MARKET_WRITABLE = ("_market_analyst", "_market_debate_r<n>_<bull|bear>")


@dataclass
class MarketToolContext:
    """시장 세션 도구의 실행 컨텍스트(계좌·브로커는 쓰지 않는다)."""

    settings: Any
    conn: sqlite3.Connection | None
    state: Path | None = None
    now: Callable[[], datetime] = field(default=lambda: datetime.now(UTC))


def tool_ctx(boot: Any) -> MarketToolContext:
    """부트 결과로 MarketToolContext 를 만든다."""
    return MarketToolContext(settings=boot.settings, conn=boot.conn, state=paths.state_dir())


def _bad_market(market: str) -> dict | None:
    if market not in ("KR", "US"):
        return {"ok": False, "error": f"market 은 KR 또는 US: {market!r}"}
    return None


def _market_turns(d: Path) -> dict[int, dict[str, DebateTurn]]:
    return tooling.load_turns(d, _MARKET_DEBATE_RE)


def _market_report(d: Path) -> dict | None:
    cp = read_checkpoint(d, "_market_analyst")
    return cp.get("data") if isinstance(cp, dict) and isinstance(cp.get("data"), dict) else None


def _market_hedge_context(ctx: MarketToolContext, market: str, inputs: dict) -> dict:
    h = ctx.settings.hedge
    return {
        "max_weight_pct": h.max_weight_pct,
        "min_weight_pct": h.min_weight_pct,
        "leverage": h.leverage,
        "instruments": {k: v.model_dump() for k, v in h.instruments.get(market, {}).items()},
        "held": (inputs.get("hedge") or {}).get("held", []),
        "state": (inputs.get("regime") or {}).get("state"),
    }


def market_bundle(
    ctx: MarketToolContext,
    *,
    market: str,
    date: date,
    kind: str,
    round: int | None = None,
    side: str | None = None,
) -> dict:
    """시장 세션 서브에이전트 입력 묶음. kind=analyst(`_market_inputs`) |
    researcher(round, side) | strategist(리포트 + 요약 + 국면·헤지·섹터 컨텍스트)."""
    if err := _bad_market(market):
        return err
    d = market_dir(date, market, ctx.state)
    inputs = (read_checkpoint(d, "_market_inputs") or {}).get("data") or {}
    if not inputs:
        return {"ok": False, "error": "_market_inputs 없음 (파이썬이 세션 앞에서 만든다)"}
    base = {"scope": "market", "market": market, "date": date.isoformat()}
    if kind == "analyst":
        return {"ok": True, "data": tooling.shrink({**base, **inputs}, BUNDLE_LIMIT_BYTES)}
    report = _market_report(d)
    if report is None:
        return {"ok": False, "error": "_market_analyst 없음 (거시 애널리스트 먼저)"}
    if kind == "researcher":
        if round is None or side not in ("bull", "bear"):
            return {
                "ok": False,
                "error": "researcher 묶음은 --round 와 --side(bull|bear) 가 필요합니다",
            }
        turns = _market_turns(d)
        own = opp = None
        if round > 0:
            prev = turns.get(round - 1, {})
            own = prev.get(side)
            opp = prev.get("bear" if side == "bull" else "bull")
            if own is None or opp is None:
                return {"ok": False, "error": f"라운드 {round - 1} 의 양측 턴이 모두 필요합니다"}
        data = {
            **base,
            "proxy": inputs.get("proxy"),
            "round": round,
            "side": side,
            "reports": [report],
            "regime": inputs.get("regime"),
            "own_previous": own.model_dump() if own else None,
            "opponent_previous": opp.model_dump() if opp else None,
        }
        return {"ok": True, "data": tooling.shrink(data, BUNDLE_LIMIT_BYTES)}
    if kind == "strategist":
        summ = read_checkpoint(d, "_market_summary")
        if summ is None:
            return {"ok": False, "error": "_market_summary 없음 (market summarize 먼저)"}
        data = {
            **base,
            "proxy": inputs.get("proxy"),
            "report": report,
            "research_summary": tooling.trader_summary(summ.get("data", summ)),
            "regime": inputs.get("regime"),
            "hedge": _market_hedge_context(ctx, market, inputs),
            "sector_weights": inputs.get("sector_weights") or [],
        }
        return {"ok": True, "data": tooling.shrink(data, BUNDLE_LIMIT_BYTES)}
    return {"ok": False, "error": f"알 수 없는 kind: {kind} (analyst|researcher|strategist)"}


def market_write(
    ctx: MarketToolContext,
    *,
    market: str,
    date: date,
    stage: str,
    payload: dict,
    agent: str | None = None,
    call_meta: dict | None = None,
) -> dict:
    """LLM 쓰기 가능 시장 단계: `_market_analyst`(AnalystReport),
    `_market_debate_r<n>_<side>`(DebateTurn)."""
    if err := _bad_market(market):
        return err
    m = _MARKET_DEBATE_RE.match(stage)
    rounds = int(getattr(ctx.settings, "debate_rounds", 0))
    if stage == "_market_analyst":
        model: type = AnalystReport
    elif m:
        model = DebateTurn
        if int(m.group(1)) > rounds:
            return {
                "ok": False,
                "error": f"라운드 {m.group(1)} 는 설정(debate_rounds={rounds})을 넘습니다",
            }
    else:
        return {
            "ok": False,
            "error": f"쓸 수 없는 단계: {stage} (허용: {', '.join(_MARKET_WRITABLE)})",
        }
    v, err = tooling.validate_payload(model, payload)
    if err:
        return err
    if m and (v.round != int(m.group(1)) or v.side != m.group(2)):
        return {
            "ok": False,
            "error": (
                f"파일명 r{m.group(1)}_{m.group(2)} 와 페이로드 "
                f"round={v.round} side={v.side} 불일치"
            ),
        }
    d = market_dir(date, market, ctx.state, create=True)
    if read_checkpoint(d, "_market_inputs") is None:
        return {"ok": False, "error": "_market_inputs 없음 (시장 세션 밖에서는 쓸 수 없다)"}
    path = write_checkpoint(d, stage, _wrap(v.model_dump(), tooling.call_meta(agent, call_meta)))
    return {"ok": True, "path": str(path), "stage": stage}


def market_summarize(ctx: MarketToolContext, *, market: str, date: date) -> dict:
    """`_market_debate_*` 를 모아 `_market_summary`(수렴 감시 포함)를 만든다."""
    if err := _bad_market(market):
        return err
    d = market_dir(date, market, ctx.state)
    return tooling.summarize_turns(d, "_market_summary", _MARKET_DEBATE_RE)


def submit_market_view(
    ctx: MarketToolContext,
    *,
    market: str,
    date: date,
    payload: dict,
    agent: str = "strategist",
    call_meta: dict | None = None,
) -> dict:
    """HedgeProposal 검증 → `_market_view.json` + `control.market_view:<market>`(오늘 날짜 포함)
    + 원장 `market_views`. 전제: `_market_analyst` 와 `_market_summary` 가 있어야 한다."""
    if err := _bad_market(market):
        return err
    d = market_dir(date, market, ctx.state)
    if _market_report(d) is None or read_checkpoint(d, "_market_summary") is None:
        return {
            "ok": False,
            "error": "_market_analyst 와 _market_summary 가 있어야 제출할 수 있습니다",
        }
    view, err = tooling.validate_payload(HedgeProposal, payload)
    if err:
        return err
    if view.market != market:
        return {
            "ok": False,
            "error": f"payload.market={view.market} 이 --market {market} 과 다릅니다",
        }
    call = tooling.call_meta(agent, call_meta)
    path = write_checkpoint(d, "_market_view", _wrap(view.model_dump(), call))
    if ctx.conn is not None:
        from trader.features.regime import market_view as mv  # 순환 import 회피(mv → tools)

        control.set_flag(
            ctx.conn,
            f"{MARKET_VIEW_FLAG}{market}",
            {"date": date.isoformat(), **view.model_dump(), "call": call},
        )
        mv.record_view(ctx.conn, today=date, market=market, view=view.model_dump())
    logger.info("시장 뷰 저장 %s: %s %.0f%%", market, view.stance, view.hedge_weight * 100)
    return {"ok": True, "path": str(path), "stance": view.stance, "hedge_weight": view.hedge_weight}


def market_view_today(conn: sqlite3.Connection | None, market: str, today: date) -> dict | None:
    """오늘 제출된 시장 뷰(없거나 다른 날짜 것이면 None)."""
    if conn is None:
        return None
    v = control.get_flag(conn, f"{MARKET_VIEW_FLAG}{market}")
    if not v or v.get("date") != today.isoformat():
        return None
    return v
