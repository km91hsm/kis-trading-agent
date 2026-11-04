"""FAKE_CLAUDE=1 스캔 세션(`screen`·`discover`): claude 를 띄우지 않고 scout·screener 의 도구 호출을
흉내 낸다. 실제 세션과 같은 도구 함수(`submit_discovery`·`submit_screen`)를 쓰므로 전제조건·스키마
검증이 그대로 적용된다(FAKE 는 KR 시세 점검을 하지 않는다 — `market_client=None`)."""

from __future__ import annotations

from typing import Any

from trader.features.scan import tools
from trader.features.scan.tools import ScanToolContext
from trader.kernel.session.fake import FakeSessionError, fake_call
from trader.kernel.session.run_store import market_dir, read_checkpoint


def _tool_ctx(ctx: Any) -> ScanToolContext:
    return ScanToolContext(
        settings=ctx.settings, conn=ctx.conn, state=ctx.state, now=ctx.now, market_client=None
    )


def run_fake_discover(ctx: Any, spec: Any) -> list[str]:
    """discover 모드(Phase 23): `_discovery_inputs` 의 news 에 태그된 첫 유니버스 종목을 발굴로
    흉내 낸다(없으면 빈 발굴). screener 는 없다."""
    market, date = spec.market, spec.date
    tctx = _tool_ctx(ctx)
    di = (read_checkpoint(market_dir(date, market, ctx.state), "_discovery_inputs") or {}).get(
        "data"
    ) or {}
    uni_by = {u.get("ticker"): u for u in di.get("universe") or []}
    pick = None
    for n in di.get("news") or []:
        for t in n.get("tickers") or []:
            if t in uni_by:
                pick = (uni_by[t], n)
                break
        if pick:
            break
    cands = []
    if pick:
        u, n = pick
        cands.append(
            {
                "name": u.get("name") or u["ticker"],
                "ticker": u["ticker"],
                "market": market,
                "tier": "universe",
                "catalyst": f"FAKE 장중 발굴: {str(n.get('title'))[:80]}",
                "source_ids": [f"news:{n.get('id')}"],
                "confidence": 0.6,
            }
        )
    tools.submit_discovery(
        tctx,
        market=market,
        date=date,
        payload={"candidates": cands, "notes": ["FAKE 모드"]},
        agent="scout",
        call_meta=fake_call("scout"),
    )
    return ["_discovery"]


def run_fake_screen(ctx: Any, spec: Any) -> list[str]:
    """screen 모드: `_discovery_inputs` 가 있으면 유니버스 첫 종목 1개를 발굴(scout 흉내)해 병합한
    뒤, 점수 있는 후보(발굴 후보 포함) 상위 5개를 고른다."""
    market, date = spec.market, spec.date
    candidates = spec.extra["candidates"]
    tctx = _tool_ctx(ctx)
    di = (read_checkpoint(market_dir(date, market, ctx.state), "_discovery_inputs") or {}).get(
        "data"
    ) or {}
    uni_by = {u.get("ticker"): u for u in di.get("universe") or []}
    # 정량 점수 순으로 첫 유니버스 종목 하나만 발굴한다(거른 종목을 되살리지 않아 순서가 유지된다)
    first = next(
        (
            uni_by[c["ticker"]]
            for c in candidates
            if c.get("score") is not None and c["ticker"] in uni_by
        ),
        None,
    )
    if first is not None:
        r = tools.submit_discovery(
            tctx,
            market=market,
            date=date,
            payload={
                "candidates": [
                    {
                        "name": first.get("name") or first["ticker"],
                        "ticker": first["ticker"],
                        "market": market,
                        "tier": "universe",
                        "catalyst": "FAKE 발굴: 뉴스 촉매",
                        "source_ids": ["market_notes"],
                        "confidence": 0.6,
                    }
                ],
                "notes": ["FAKE 모드"],
            },
            agent="scout",
            call_meta=fake_call("scout"),
        )
        if r["ok"]:
            candidates = r["candidates"]
    scored = [
        c for c in candidates if c.get("score") is not None or c.get("discovery")
    ] or candidates
    picks = [
        {
            "ticker": c["ticker"],
            "reason": "FAKE: 뉴스 발굴" if c.get("discovery") else "FAKE: 정량 상위",
        }
        for c in scored[:5]
    ]
    r = tools.submit_screen(
        tctx,
        market=market,
        date=date,
        payload={"picks": picks, "rejected": [c["ticker"] for c in scored[5:]]},
        agent="screener",
        call_meta=fake_call("screener"),
    )
    if not r["ok"]:
        raise FakeSessionError(r["error"])
    return ["_screen"]
