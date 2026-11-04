"""FAKE_CLAUDE=1 모드: claude 를 띄우지 않고 오케스트레이터의 도구 호출 순서를 파이썬으로 흉내 낸다.

실제 세션과 같은 도구 함수(`tools.*`)를 쓰므로 전제조건·스키마 검증·체크포인트 재개가 그대로
적용된다. 응답은 고정 JSON(애널리스트 NEUTRAL, bull BULLISH 0.7 / bear BEARISH 0.6, 트레이더 HOLD).
reflect·discover·screen·market·fo 의 FAKE 는 해당 마일스톤에서 더한다."""

from __future__ import annotations

import logging
from datetime import date

from trader.features.spot import tools
from trader.kernel.lane.types import LaneContext
from trader.kernel.session.fake import fake_call
from trader.kernel.session.run_store import list_checkpoints, read_checkpoint, run_dir
from trader.kernel.session.runner import SessionSpec

logger = logging.getLogger("trader.spot")

ANALYST_ROLES = ("technical", "fundamental", "news_macro", "sentiment")


def fake_report(ticker: str, asof: str, role: str) -> dict:
    return {
        "ticker": ticker,
        "asof": asof,
        "role": role,
        "summary": f"FAKE {role} 리포트",
        "bullets": ["FAKE 근거"],
        "signal": "NEUTRAL",
        "score": 0.0,
        "confidence": 0.5,
        "horizon": "short",
        "key_risks": [],
        "counter_evidence": [],
        "evidence": [{"claim": "FAKE", "source_id": f"{role}"}],
        "data_gaps": ["FAKE 모드"],
    }


def fake_turn(side: str, round_no: int) -> dict:
    bull = side == "bull"
    return {
        "side": side,
        "round": round_no,
        "stance": "BULLISH" if bull else "BEARISH",
        "valuation_view": "UNDERVALUED" if bull else "OVERVALUED",
        "arguments": [f"FAKE {side} 논거"],
        "rebuttals": [f"FAKE 반박 r{round_no}"] if round_no > 0 else [],
        "timing": {"action": "WAIT", "condition": None, "price_zone": None},
        "conviction": 0.7 if bull else 0.6,
        "what_would_change_my_mind": ["FAKE"],
    }


def fake_decision(ticker: str) -> dict:
    return {
        "ticker": ticker,
        "action": "HOLD",
        "target_weight": 0.0,
        "order_type": "LIMIT",
        "limit_price": None,
        "stop_loss": None,
        "take_profit": None,
        "horizon_days": 5,
        "confidence": 0.5,
        "rationale": "FAKE 모드: 기본값 HOLD",
        "invalidation_conditions": [],
        "memory_ids_used": [],
    }


def run_fake_ticker(
    ctx: tools.ToolContext,
    ticker: str,
    market: str,
    d: date,
    debate_rounds: int,
    trigger: dict | None = None,
) -> list[str]:
    """한 종목에 대해 남은 단계만 실행하고 새로 쓴 단계 이름을 돌려준다.

    `trigger` 가 있으면(장중 재평가, M5) 분석 단계를 채운 뒤 `31_decision_r<n>` 으로 제출한다."""
    rd = run_dir(d, market, ticker, ctx.state)
    inputs = read_checkpoint(rd, "00_inputs")
    if inputs is None:
        raise tools.ToolError(f"00_inputs 없음: {market} {ticker} (먼저 collect)")
    asof = str((inputs.get("meta") or {}).get("asof", ""))
    written: list[str] = []

    def write(stage: str, payload: dict, agent: str) -> None:
        if stage in list_checkpoints(rd):
            return
        r = tools.checkpoint_write(
            ctx,
            ticker=ticker,
            market=market,
            date=d,
            stage=stage,
            payload=payload,
            agent=agent,
            call_meta=fake_call(agent),
        )
        if not r["ok"]:
            raise tools.ToolError(r["error"])
        written.append(stage)

    for role in ANALYST_ROLES:
        write(f"10_analyst_{role}", fake_report(ticker, asof, role), f"analyst-{role}")
    stages = list_checkpoints(rd)
    has_debate = any(s.startswith("20_debate_") for s in stages)
    skipped = False
    if not has_debate and "25_research_summary" not in stages:
        # 오케스트레이터 프롬프트와 같은 순서: 토론 전에 summarize — 합의면 토론 생략
        r = tools.checkpoint_summarize(ctx, ticker=ticker, market=market, date=d)
        if r["ok"]:
            skipped = True
            written.append("25_research_summary")
        elif not r.get("debate_required"):
            raise tools.ToolError(r["error"])
    elif not has_debate:
        skipped = True  # 이미 생략 요약이 있다
    if not skipped:
        before = len(written)
        for n in range(0, debate_rounds + 1):
            for side in ("bull", "bear"):
                write(f"20_debate_r{n}_{side}", fake_turn(side, n), f"researcher-{side}")
        debate_changed = len(written) > before
        # 토론 턴을 새로 썼으면 요약도 다시 만든다 (오케스트레이터 프롬프트와 같은 규칙)
        if debate_changed or "25_research_summary" not in list_checkpoints(rd):
            r = tools.checkpoint_summarize(ctx, ticker=ticker, market=market, date=d)
            if not r["ok"]:
                raise tools.ToolError(r["error"])
            written.append("25_research_summary")
    if trigger is not None:
        n = 1 + sum(1 for s in list_checkpoints(rd) if s.startswith("31_decision_r"))
        stage = f"31_decision_r{n}"
        r = tools.submit_decision(
            ctx,
            ticker=ticker,
            market=market,
            date=d,
            payload=fake_decision(ticker),
            agent="trader",
            call_meta=fake_call("trader"),
            stage=stage,
        )
        if not r["ok"]:
            raise tools.ToolError(r["error"])
        return written + [stage]
    if "30_decision" not in list_checkpoints(rd):
        r = tools.submit_decision(
            ctx,
            ticker=ticker,
            market=market,
            date=d,
            payload=fake_decision(ticker),
            agent="trader",
            call_meta=fake_call("trader"),
        )
        if not r["ok"]:
            raise tools.ToolError(r["error"])
        written.append("30_decision")
    return written


def run_fake_analyze(lane_ctx: LaneContext, spec: SessionSpec) -> list[str]:
    """`SessionKind.fake` 진입점: spec.tickers 마다 `run_fake_ticker`. ToolContext 는
    accounts·inputs 포트로 조립하고 dry_run 은 spec 을 따른다."""
    ctx = tools.ctx_from_ports(
        settings=lane_ctx.settings,
        conn=lane_ctx.conn,
        state=lane_ctx.state,
        ports=lane_ctx.ports,
        notifier=lane_ctx.notifier,
        dry_run=spec.dry_run,
        now=lane_ctx.now,
    )
    rounds = int(getattr(lane_ctx.settings, "debate_rounds", 0))
    written: list[str] = []
    for t in spec.tickers:
        written += run_fake_ticker(ctx, t["ticker"], spec.market, spec.date, rounds)
    return written


def run_fake_resume(lane_ctx: LaneContext, spec: SessionSpec) -> list[str]:
    """`SessionKind.fake` 진입점(resume): 종목마다 `31_decision_r<n>` 을 제출한다."""
    ctx = tools.ctx_from_ports(
        settings=lane_ctx.settings,
        conn=lane_ctx.conn,
        state=lane_ctx.state,
        ports=lane_ctx.ports,
        notifier=lane_ctx.notifier,
        dry_run=spec.dry_run,
        now=lane_ctx.now,
    )
    rounds = int(getattr(lane_ctx.settings, "debate_rounds", 0))
    written: list[str] = []
    for t in spec.tickers:
        written += run_fake_ticker(
            ctx, t["ticker"], spec.market, spec.date, rounds, trigger=spec.extra["trigger"]
        )
    return written


__all__ = [
    "ANALYST_ROLES",
    "fake_decision",
    "fake_report",
    "fake_turn",
    "run_fake_analyze",
    "run_fake_resume",
    "run_fake_ticker",
]
