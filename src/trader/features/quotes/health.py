"""quotes 운영 점검 영역: 체결통보 리스너가 KIS 웹소켓 세션을 쥔 동안(control
`ws_session_busy:<env>` = `{"until": UTC ISO}`, until 이 미래) 시세 허브가 양보 중 → watch."""

from __future__ import annotations

import json
from datetime import UTC, datetime

from trader.kernel.ops.types import Item, OpsContext
from trader.kernel.registry.types import HealthArea

BUSY_PREFIX = "ws_session_busy:"


def _busy_until(raw: str | None) -> datetime | None:
    """플래그 값 `{"until": UTC ISO}` 의 until. 없거나 깨졌으면 None, tz 없으면 UTC."""
    try:
        value = json.loads(raw) if raw else None
        until = value.get("until") if isinstance(value, dict) else None
        dt = datetime.fromisoformat(str(until)) if until else None
    except (TypeError, ValueError):
        return None
    if dt is not None and dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt


def check(ctx: OpsContext) -> list[Item]:
    rows = ctx.conn.execute(
        "SELECT key, value FROM control WHERE key LIKE ? ORDER BY key", (BUSY_PREFIX + "%",)
    ).fetchall()
    out: list[Item] = []
    for r in rows:
        until = _busy_until(r["value"])
        if until is None or until <= ctx.now:
            continue
        env = r["key"][len(BUSY_PREFIX) :]
        text = (
            f"{env} 웹소켓 세션을 체결통보 리스너가 쓰는 중 — 실시간 시세 양보"
            f"(until {until.isoformat()})"
        )
        out.append(Item(f"quotes.session_busy.{env}", "watch", text, link="/system"))
    return out


AREA = HealthArea("quotes", check)
