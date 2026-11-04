"""FAKE_CLAUDE=1 파생 세션(옛 `agents/fake.run_fake_fo`): claude 를 띄우지 않고 오케스트레이터의
도구 호출 순서를 흉내 낸다. 실제 세션과 같은 도구 함수(`tools.fo_write`·`fo_summarize`·
`submit_fo_decision`)를 쓰므로 전제조건·스키마 검증이 그대로 적용된다.

fo·fo_resume 모드: 애널리스트 3역할(NEUTRAL) → r0 토론 → 요약 → 봉투. 아침은 FLAT HOLD(request_id 는
`_fo_inputs` 의 것), 재평가는 stdin 봉투(`spec.extra["position"]` — 계좌·포지션·버전·request_id)를
그대로 되돌리는 HOLD — 파이썬 게이트·훅이 어차피 기록·적용을 맡는다. 실행기 컨텍스트는
`LaneContext.ext["fo"]`(`fo/session.run`·재평가가 넣는다)."""

from __future__ import annotations

from typing import Any

from trader.kernel.session.fake import ensure_ok, fake_call
from trader.kernel.session.run_store import market_dir, read_checkpoint


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


def run_fake_fo(lane_ctx: Any, spec: Any) -> list[str]:
    """`SessionKind.fake` 진입점(lane_ctx 는 `LaneContext`, spec 은 `SessionSpec`). 쓴 단계 이름을
    순서대로 돌려준다. `spec.dry_run` 이면 실행기 컨텍스트도 dry_run(옛 `run_session` 동작)."""
    from trader.features.fo import tools

    fo = (getattr(lane_ctx, "ext", None) or {}).get("fo")
    if fo is not None and spec.dry_run:
        fo.dry_run = True
    ctx = tools.FoToolContext(
        settings=lane_ctx.settings, conn=lane_ctx.conn, state=lane_ctx.state, now=lane_ctx.now,
        dry_run=bool(spec.dry_run), fo=fo,
    )  # fmt: skip
    date = spec.date
    position = (spec.extra or {}).get("position")
    d = market_dir(date, tools.FO_MARKET, ctx.state)
    inputs = (read_checkpoint(d, tools.FO_INPUTS) or {}).get("data") or {}
    proxy = (inputs.get("meta") or {}).get("proxy") or "KOSPI200"
    written: list[str] = []
    for role, agent in (
        ("technical", "analyst-technical"),
        ("sentiment", "analyst-sentiment"),
        ("news_macro", "analyst-market"),
    ):
        stage = f"_fo_analyst_{role}"
        ensure_ok(tools.fo_write(
            ctx, date=date, stage=stage, payload=fake_report(proxy, date.isoformat(), role),
            agent=agent, call_meta=fake_call(agent),
        ))  # fmt: skip
        written.append(stage)
    for side in ("bull", "bear"):
        stage = f"_fo_debate_r0_{side}"
        ensure_ok(tools.fo_write(
            ctx, date=date, stage=stage, payload=fake_turn(side, 0),
            agent=f"researcher-{side}", call_meta=fake_call(f"researcher-{side}"),
        ))  # fmt: skip
        written.append(stage)
    ensure_ok(tools.fo_summarize(ctx, date=date))
    written.append(tools.FO_SUMMARY)
    decision = {
        "intent": "HOLD",
        "direction": "FLAT",
        "vehicle": "FUTURES",
        "target_leverage": 0.0,
        "confidence": 0.5,
        "rationale": "FAKE 모드: 기본값 HOLD(FLAT)",
        "invalidation_conditions": [],
    }
    if position:
        envelope = {
            "decision": decision,
            "account_id": position.get("account_id"),
            "position_id": position.get("position_id"),
            "expected_version": position.get("expected_version"),
            "request_id": position.get("request_id"),
        }
        dec_stage: str | None = (
            f"_fo_decision_r{position.get('round')}_{position.get('account_id')}"
        )
    else:
        request_id = (inputs.get("meta") or {}).get("request_id")
        envelope = {"decision": decision, "request_id": request_id}
        dec_stage = None
    r = tools.submit_fo_decision(
        ctx, date=date, payload=envelope, stage=dec_stage, agent="fo-trader",
        call_meta=fake_call("fo-trader"),
    )  # fmt: skip
    ensure_ok(r)
    written.append(r["stage"])
    return written
