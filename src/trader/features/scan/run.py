"""`run_scan`: 정량 필터 → screener 세션 → 오늘 분석 대상 목록(`_scan` 체크포인트)과 알림.
데몬 scan 작업(`ports["scan"].run_scan`)과 `trader scan run` 이 같이 쓴다."""

from __future__ import annotations

import logging
from typing import Any

from trader.features.ingest import api as ingest_api
from trader.features.scan import discovery as discovery_mod
from trader.features.scan import quant, ranker, screener, watchlist
from trader.kernel.notify import messages
from trader.kernel.session import calls
from trader.kernel.session.run_store import market_dir, run_date_for, write_checkpoint

logger = logging.getLogger("trader.scan")


def run_scan(ctx: Any, *, market: str, cancel: Any | None = None, dry_run: bool = False) -> dict:
    """{market, date, candidates, picks, analyze_targets, session, discovery, cap_hit, ml_model}.
    `_scan` 체크포인트는 봉투 없이 이 dict 그대로 쓴다. `dry_run` 이면 세션 없이 정량 상위만."""
    if cancel is not None and cancel.is_set():
        return {"skipped": "종료 중"}
    universe = ingest_api.load_universe()
    today = run_date_for(market, ctx.now())
    ml = ranker.current(ctx.state, market=market, registry=(ctx.ext or {}).get("registry"))
    runtime_watch = watchlist.get_watchlist(ctx.conn, market=market)
    cands = quant.candidates(
        universe,
        market,
        state=ctx.state,
        rank_fn=ml[0] if ml else None,
        watch_extra=runtime_watch,
        asof=today,  # 과거 시점 스캔이 그 뒤 피처 파일을 보지 않도록
    )
    cap_hit = dry_run or (
        calls.sessions_today(ctx.conn, ctx.now()) >= ctx.settings.llm.daily_session_cap
    )
    # 발굴 입력(네이버 블로그)용 http: 주입(`ext["http"]`)이면 그대로, 없으면 만들어 끝에 닫는다
    with ingest_api.owned_client((ctx.ext or {}).get("http"), timeout=15) as http:
        r = screener.run_screen(
            ctx,
            market=market,
            date=today,
            candidates=cands,
            cap_hit=cap_hit,
            watch=[*universe.watch, *runtime_watch],
            asof=ctx.now(),
            cancel=cancel,
            universe=universe,
            discovery=True,
            http=http,
        )
    out = {
        "market": market,
        "date": today.isoformat(),
        "candidates": cands,
        **r,
        "cap_hit": cap_hit,
        "ml_model": ml[1] if ml else None,
    }
    write_checkpoint(market_dir(today, market, ctx.state, create=True), "_scan", out)
    if ctx.notifier is not None:
        try:
            ctx.notifier.send(scan_line(out))
        except Exception:  # noqa: BLE001 - 알림 실패가 scan 결과를 바꾸지 않는다
            logger.debug("scan 알림 실패", exc_info=True)
    return out


def scan_body(scan: dict) -> str:
    """scan 결과 본문: picks·분석 대상·뉴스 발굴 요약. 알림(`scan_line`)과 status 절 공용."""
    picks = [p.get("ticker") for p in scan.get("picks") or []]
    n_t = len(scan.get("analyze_targets") or [])
    cap = " · 세션 상한" if scan.get("cap_hit") else ""
    return (
        f"{scan.get('market')} {scan.get('date')}: 선정 {len(picks)}"
        f"({', '.join(str(p) for p in picks) or '-'}) · 분석 대상 {n_t} · "
        f"{discovery_mod.line(scan.get('discovery'))}{cap}"
    )


def scan_line(scan: dict) -> str:
    """scan 결과 알림 한 줄(`[스캔] …`)."""
    return messages.event("스캔", scan_body(scan))
