"""quotes 공개면. 다른 기능은 이 모듈만 import 한다(quotes 는 accounts 를 import 하지 않는다)."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from typing import Any

from trader.features.quotes import fx


def fx_rate(settings: Any, conn: sqlite3.Connection, pair: str = "USDKRW") -> dict | None:
    """환율 `{pair, rate: str(Decimal), as_of, source: "kis"|"macro"}`. 원천이 없거나 모르는 쌍이면
    None. KST 하루 1회 control `fx_cache:<pair>` 캐시(`fx.rate`).
    전부 실패하면 `fail_at` 로 300초 음성 캐시하고, 이전 레코드가 있으면
    `stale: true` 로 돌려준다."""
    return fx.rate(conn, pair, now=datetime.now(UTC))


__all__ = ["fx_rate"]
