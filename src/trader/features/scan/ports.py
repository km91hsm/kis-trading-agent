"""scan 포트: 레인이 `Boot.ports["scan"]` 로 받는 종목 → 오늘 상위 테마·섹터·관심종목 조회
(커널 `execution.ports.ScanPort`). 판정 로직은 `membership` 에 있고 여기서는 설정만 입혀 부른다."""

from __future__ import annotations

import logging
import sqlite3
from collections.abc import Callable, Iterable, Mapping
from datetime import date
from typing import Any

from trader.features.ingest.api import load_sectors
from trader.features.scan import membership, run, sector_suggest, theme, watchlist
from trader.features.scan.settings import load_events
from trader.kernel.platform import paths
from trader.kernel.registry.types import Port

logger = logging.getLogger("trader.scan")


class ScanPortImpl:
    def __init__(
        self, settings: Any, registry: Any = None, ports: Mapping[str, Any] | None = None
    ) -> None:
        self._settings = settings
        self._registry = registry
        # `Boot.ports` 자체 — 시세 클라이언트(accounts 포트)는 classify 때 풀어 쓴다
        self._ports = ports if ports is not None else {}

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

    def run_scan(
        self, ctx: Any, *, market: str, cancel: Any | None = None, dry_run: bool = False
    ) -> dict:
        """정량 필터 → screener 세션 → `_scan` 체크포인트·알림(데몬 scan 작업이 부른다)."""
        return run.run_scan(ctx, market=market, cancel=cancel, dry_run=dry_run)

    def sector_of(self, market: str) -> Callable[[str], str | None]:
        """종목 → 섹터: sectors.yaml 우선, 없으면 자동 분류 캐시(`state/cache/sector_auto.json`),
        그래도 없으면 None."""
        return sector_suggest.resolver(load_sectors(), market, state=paths.state_dir())

    def watchlist(self, conn: sqlite3.Connection, market: str | None) -> list[str]:
        """런타임 관심종목(`/watch`·웹). market 을 주면 그 시장 종목만 접두사 없이."""
        return watchlist.get_watchlist(conn, market)

    def classify(self, conn: sqlite3.Connection, market: str, tickers: Iterable[str]) -> None:
        """yaml·자동 캐시에 없는 종목만 KIS 업종으로 대분류해 `state/cache/sector_auto.json` 에
        적는다(`sector_suggest.auto_classify`). 종목명(`ticker_names`)으로 ETF 상표를 묶는다.
        시세 클라이언트(accounts 포트)가 없으면 건너뛴다."""
        acc = self._ports.get("accounts")
        mc = acc.market_client() if acc is not None else None
        if mc is None:
            logger.info("시세 클라이언트 없음 — 섹터 자동 분류 생략(%s)", market)
            return
        sector_suggest.auto_classify(
            load_sectors(),
            mc,
            market=market,
            tickers=tickers,
            state=paths.state_dir(),
            names=theme._ticker_names(conn, market),
        )


def build_port(boot: Any) -> ScanPortImpl:
    return ScanPortImpl(
        boot.settings, getattr(boot, "registry", None), getattr(boot, "ports", None)
    )


PORT = Port("scan", build_port)
