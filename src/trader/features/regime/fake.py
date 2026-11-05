"""FAKE_CLAUDE=1 시장 세션: claude 를 띄우지 않고 오케스트레이터의 도구 호출 순서를 흉내 낸다.

실제 세션과 같은 도구 함수(`tools.market_*`·`submit_market_view`)를 쓰므로 전제조건·스키마
검증이 그대로 적용된다. 거시 리포트 NEUTRAL → 토론 r0(+반박 r1) → 요약 → HedgeProposal
(국면이 bear 면 RISK_OFF 0.10, 아니면 RISK_ON 0.0 — 파이썬 규칙이 어차피 bear 아니면 진입 안 함)."""

from __future__ import annotations

from datetime import date
from typing import Any

from trader.features.regime import tools
from trader.kernel.session.fake import ensure_ok, fake_call, fake_turn
from trader.kernel.session.run_store import market_dir, read_checkpoint


def _report(ticker: str, asof: str) -> dict:
    return {
        "ticker": ticker,
        "asof": asof,
        "role": "news_macro",
        "summary": "FAKE news_macro 리포트",
        "bullets": ["FAKE 근거"],
        "signal": "NEUTRAL",
        "score": 0.0,
        "confidence": 0.5,
        "horizon": "short",
        "key_risks": [],
        "counter_evidence": [],
        "evidence": [{"claim": "FAKE", "source_id": "news_macro"}],
        "data_gaps": ["FAKE 모드"],
    }


def write_fake_market(
    ctx: tools.MarketToolContext, *, market: str, date: date, debate_rounds: int
) -> list[str]:
    """market 모드 단계를 순서대로 쓰고 쓴 단계 이름을 돌려준다."""
    d = market_dir(date, market, ctx.state)
    inputs = (read_checkpoint(d, "_market_inputs") or {}).get("data") or {}
    proxy = inputs.get("proxy") or tools.MARKET_PROXY_LABEL.get(market, market)
    written: list[str] = []
    ensure_ok(
        tools.market_write(
            ctx,
            market=market,
            date=date,
            stage="_market_analyst",
            payload=_report(proxy, date.isoformat()),
            agent="analyst-market",
            call_meta=fake_call("analyst-market"),
        )
    )
    written.append("_market_analyst")
    for rnd in range(0, min(debate_rounds, 1) + 1):
        for side in ("bull", "bear"):
            agent = f"market-researcher-{side}"
            ensure_ok(
                tools.market_write(
                    ctx,
                    market=market,
                    date=date,
                    stage=f"_market_debate_r{rnd}_{side}",
                    payload=fake_turn(side, rnd),
                    agent=agent,
                    call_meta=fake_call(agent),
                )
            )
            written.append(f"_market_debate_r{rnd}_{side}")
    ensure_ok(tools.market_summarize(ctx, market=market, date=date))
    written.append("_market_summary")
    bear = (inputs.get("regime") or {}).get("state") == "bear"
    ensure_ok(
        tools.submit_market_view(
            ctx,
            market=market,
            date=date,
            payload={
                "market": market,
                "stance": "RISK_OFF" if bear else "RISK_ON",
                "hedge_weight": 0.10 if bear else 0.0,
                "leveraged": False,
                "confidence": 0.5,
                "horizon_days": 20,
                "rationale": "FAKE 모드: 국면 지표를 그대로 따름",
                "invalidation_conditions": [],
            },
            agent="strategist",
            call_meta=fake_call("strategist"),
        )
    )
    written.append("_market_view")
    return written


def run_fake_market(ctx: Any, spec: Any) -> list[str]:
    """`SessionKind.fake` 진입점(ctx 는 `LaneContext`, spec 은 `SessionSpec`)."""
    tool_ctx = tools.MarketToolContext(
        settings=ctx.settings, conn=ctx.conn, state=ctx.state, now=ctx.now
    )
    return write_fake_market(
        tool_ctx,
        market=spec.market,
        date=spec.date,
        debate_rounds=int(getattr(ctx.settings, "debate_rounds", 0)),
    )
