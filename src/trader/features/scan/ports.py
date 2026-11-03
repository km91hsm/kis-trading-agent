"""scan 포트: 레인이 `Boot.ports["scan"]` 로 받는 종목 → 오늘 상위 테마 조회
(커널 `execution.ports.ScanPort`). 판정 로직은 `membership` 에 있고 여기서는 설정만 입혀 부른다."""

from __future__ import annotations

import logging
import sqlite3
from collections.abc import Callable
from datetime import date
from typing import Any

from trader.features.scan import membership
from trader.features.scan.settings import load_events
from trader.kernel.registry.types import Port

logger = logging.getLogger("trader.scan")


class ScanPortImpl:
    def __init__(self, settings: Any, registry: Any = None) -> None:
        self._settings = settings
        self._registry = registry

    def theme_of(
        self, conn: sqlite3.Connection, market: str, today: date
    ) -> Callable[[str], list[str] | None]:
        """종목 → 오늘 상위 테마·shock 사건 라벨(`사건:<label>`). 소속이 없으면 None.
        `theme.enabled`·`events.enabled` 가 꺼진 쪽은 보지 않는다(실행 게이트라 `asof` 없음).
        사건 설정(`events.yaml`)을 못 읽으면 사건 쪽만 끄고 테마는 계속 본다(옛 동작) — 예외가
        올라가면 레인이 테마 안전장치까지 통째로 건너뛴다."""
        cfg = self._settings.theme
        try:
            events_enabled = bool(load_events(self._registry).enabled)
        except Exception as e:  # noqa: BLE001 - 사건 설정 오류가 테마 판정을 막지 않게
            logger.warning("사건 설정 읽기 실패 — 사건 라벨 없이 테마만 본다: %s", e)
            events_enabled = False
        result = membership.of(
            conn,
            market,
            today,
            theme_top_n=cfg.top_n,
            theme_enabled=cfg.enabled,
            events_enabled=events_enabled,
        )
        m = result["map"]
        return lambda ticker: list(m[ticker]) if m.get(ticker) else None


def build_port(boot: Any) -> ScanPortImpl:
    return ScanPortImpl(boot.settings, getattr(boot, "registry", None))


PORT = Port("scan", build_port)
