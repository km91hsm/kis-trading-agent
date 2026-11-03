"""아침 브리핑(Stock docs/06 「07:30 브리핑」): 밤사이 미국장 지수 변화·국면·시장 세션 제안·밤사이
뉴스·오늘 일정·보유 종목을 haiku 단발 호출(`ask_json`, 에이전트 `briefing`)로 5줄 요약해
텔레그램으로 보낸다. **사람이 읽는 요약 전용** — 결정·게이트·주문은 이 결과를 읽지 않는다.
구독 사용률이 soft 이상이면(`session_allowance(kind="briefing")` 가 resume 만 허용하는 구간)
건너뛴다. 하루 상한 `briefing.daily_cap`(분석 세션 상한과 별도, `agent_calls.agent='briefing'`)."""

from __future__ import annotations

import logging
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any

from trader.features.briefing import messages
from trader.features.briefing.schemas import Briefing
from trader.features.evaluation import api as evaluation_api
from trader.features.ingest import api as ingest_api
from trader.features.regime import api as regime_api
from trader.features.saveticker import api as saveticker_api
from trader.kernel.config import KST
from trader.kernel.execution.ledger import latest_positions
from trader.kernel.ledger import names
from trader.kernel.registry.types import AgentDef
from trader.kernel.session.quick import ask_json
from trader.kernel.session.run_store import run_date_for
from trader.kernel.session.usage import session_allowance

logger = logging.getLogger("trader.briefing")

NEWS_HOURS = 12
NEWS_LIMIT = 15
FAKE_BRIEFING = {"lines": ["FAKE 모드: 아침 브리핑 없음"], "uncertain": [], "confidence": 0.5}
AGENT = AgentDef("briefing", Path(__file__).parent / "agents" / "briefing.md", Briefing)


def build_inputs(
    conn: sqlite3.Connection,
    settings: Any,
    accounts: list,
    *,
    now: datetime,
    market_client: Any = None,
    saveticker_root: Path | None = None,
) -> dict:
    kst_today = now.astimezone(KST).date()  # 07:20 KST 는 UTC 로 아직 전날이다(Codex 리뷰)
    us = None
    idx = evaluation_api.index_table(market_client, settings.hedge, "US", days=5, now=now)
    if not idx.empty and len(idx) >= 2:
        sym = str(idx.columns[0])
        c0, c1 = float(idx[sym].iloc[-2]), float(idx[sym].iloc[-1])
        us = {"symbol": sym, "close": c1, "change_pct": round(c1 / c0 - 1, 4) if c0 else None}
    views_by_market = {}
    for m in ("KR", "US"):
        v = regime_api.view_today(conn, m, run_date_for(m, now)) or None
        views_by_market[m] = (
            {k: v.get(k) for k in ("stance", "confidence", "rationale")} if v else None
        )
    news = {
        m: [
            {k: n.get(k) for k in ("title", "source", "published_at", "tickers")}
            for n in ingest_api.recent(conn, m, asof=now, hours=NEWS_HOURS, limit=NEWS_LIMIT)
        ]
        for m in ("US", "KR")
    }
    holdings = []
    for a in accounts:
        for m in a.markets:
            snap = latest_positions(conn, a.id, m) or {}
            nav = float(snap.get("nav") or 0)
            for p in snap.get("positions") or []:
                if float(p.get("eval_amount") or 0) <= 0 or nav <= 0:
                    continue
                holdings.append(
                    {
                        "market": m,
                        "ticker": p["ticker"],
                        "name": names.resolve(conn, m, p["ticker"]),
                        "weight": round(float(p["eval_amount"]) / nav, 4),
                    }
                )
    calendar = (
        saveticker_api.upcoming(saveticker_root, asof=now, days=2) if saveticker_root else []
    )
    return {
        "date": kst_today.isoformat(),
        "us_overnight": us,
        "regime": regime_api.summary(conn, ["KR", "US"], today=kst_today),
        "market_view": views_by_market,
        "news": news,
        "calendar": calendar[:10],
        "holdings": holdings,
    }


def run(
    conn: sqlite3.Connection,
    settings: Any,
    accounts: list,
    *,
    now: datetime,
    notifier: Any,
    market_client: Any = None,
    saveticker_root: Path | None = None,
    runner: Any = None,
    cancel: Any = None,
) -> dict:
    cfg = settings.briefing
    if not cfg.enabled:
        return {"skipped": "꺼짐"}
    ok, why = session_allowance(conn, settings, kind="briefing", now=now)
    if not ok:
        return {"skipped": f"감속: {why}"}
    payload = build_inputs(
        conn,
        settings,
        accounts,
        now=now,
        market_client=market_client,
        saveticker_root=saveticker_root,
    )
    got = ask_json(
        settings,
        conn,
        agent=AGENT,
        payload=payload,
        schema=Briefing,
        cap=cfg.daily_cap,
        timeout_s=cfg.timeout_s,
        runner=runner,
        now=lambda: now,
        cancel=cancel,
        fake=lambda: FAKE_BRIEFING,
    )
    if got is None:
        return {"skipped": "판정 없음(상한·실패)"}
    notifier.send(messages.briefing(got.lines, got.uncertain))
    return {"lines": len(got.lines)}
