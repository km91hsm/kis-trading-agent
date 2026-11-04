"""quotes 공개면. 다른 기능은 이 모듈만 import 한다(quotes 는 accounts 를 import 하지 않는다)."""

from __future__ import annotations

import sqlite3
from typing import Any


def fx_rate(settings: Any, conn: sqlite3.Connection, pair: str = "USDKRW") -> dict | None:
    """환율 `{pair, rate, as_of}`. 아직 시세 원천이 없어 None(스텁) — Task 9 가 채운다."""
    return None
