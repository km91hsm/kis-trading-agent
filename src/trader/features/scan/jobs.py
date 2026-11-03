"""scan 잡·훅 본문: 테마 열기 interval 잡(`{market}_theme`), 사건 감지(`events_check`)와
`news.post_ingest` 훅(`events_post_ingest`)."""

from __future__ import annotations

import logging
import sqlite3
from datetime import datetime
from typing import Any

from trader.features.saveticker import api as saveticker_api
from trader.features.scan import events, messages, theme
from trader.features.scan.settings import load_events
from trader.kernel.registry.types import JobSpec
from trader.kernel.session.run_store import run_date_for

logger = logging.getLogger("trader.scan")


def theme_body(deps, job) -> dict:
    """테마 열기 감지 1회. 결과를 control·원장에 남기고 상위 테마가 바뀔 때만 알림(장중 창·휴장은
    게이트 `monitor` 가 본다). 시세는 `ports["accounts"]`, 세이브티커 폴더는 sources.yaml."""
    market = job.market
    port = (deps.ports or {}).get("accounts")
    root = saveticker_api.resolve_dir(saveticker_api.load_cfg(registry=deps.registry).dir)
    conn = deps.conn_factory()
    try:
        heat = theme.compute(
            conn,
            deps.settings,
            market=market,
            today=run_date_for(market, deps.now()),
            now=deps.now(),
            market_client=port.market_client() if port is not None else None,
            saveticker_root=root,
        )
        msg = theme.record(conn, heat, top_n=deps.settings.theme.top_n)
    finally:
        conn.close()
    if msg and deps.notifier is not None:
        deps.notifier.send(msg)
    return {"themes": len(heat["themes"]), "alerted": bool(msg), **heat["signals"]}


THEME = JobSpec(
    id="{market}_theme", kind="theme", trigger="interval", per_market=True,
    schedule_key="theme.interval_s", interval_s=1800, enabled=lambda s: s.theme.enabled,
    gate="monitor", heavy=False, misfire="default", catch_up="none", start_soon_s=60,
    body=theme_body,
)  # fmt: skip


def events_check(
    conn: sqlite3.Connection, market: str, *, cfg: Any, now: datetime, notifier: Any
) -> dict:
    """뉴스 저장소로 사건을 감지해 `control.events:<시장>` 에 남기고, 오늘 처음 감지된 사건은
    `[사건 감지]` 알림 1건. 매수는 하지 않는다 — 다음 scan 의 scout 가 수혜 묶음에서 고르고,
    일반 분석·리스크 게이트를 거친다."""
    if not cfg.enabled:
        return {"skipped": "events.enabled=false"}
    today = run_date_for(market, now)
    found = events.detect(conn, cfg, market=market, asof=now)
    events.record(conn, market, today, found, now=now)
    fresh = events.new_for_alert(conn, market, today, found)
    for ev in fresh if notifier is not None else []:
        try:
            notifier.send(messages.event_detected(market=market, event=ev))
        except Exception:  # noqa: BLE001
            logger.debug("사건 감지 알림 실패", exc_info=True)
    return {"detected": [e["id"] for e in found], "alerted": [e["id"] for e in fresh]}


def events_post_ingest(
    *,
    conn: sqlite3.Connection,
    markets: Any,
    now: datetime,
    result: dict | None = None,
    notifier: Any = None,
    **_: Any,
) -> dict:
    """`news.post_ingest` 훅: 시장별 사건 감지. 시장 하나의 실패는 그 시장만 error 로 남긴다."""
    cfg = load_events()
    out: dict[str, dict] = {}
    for market in markets:
        try:
            out[f"events:{market}"] = events_check(
                conn, market, cfg=cfg, now=now, notifier=notifier
            )
        except Exception as e:  # noqa: BLE001
            logger.warning("사건 감지 실패 %s: %s", market, e)
            out[f"events:{market}"] = {"error": f"{type(e).__name__}: {e}"}
    return out
