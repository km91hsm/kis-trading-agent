"""웹 라우터가 `request.app.state.deps` 로 받는 의존성 묶음."""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from trader.kernel.registry.types import Feature


@dataclass(frozen=True)
class WebDeps:
    """웹 셸이 쓰는 의존성. `boot` 가 만들고 라우터는 읽기만 한다."""

    settings: Any
    conn_factory: Callable[[], sqlite3.Connection]
    state: Path
    token: str | None
    dist: Path
    now: Callable[[], datetime]
    extra_hosts: tuple[str, ...] = ()
    features: list[Feature] = ()  # type: ignore[assignment]
