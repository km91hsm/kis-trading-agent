"""regime 포트: 레인이 `Boot.ports["regime"]` 로 받는 국면·헤지·킬스위치·블랙아웃 구현체
(커널 `execution.ports.RegimePort`)."""

from __future__ import annotations

import sqlite3
from datetime import date
from decimal import Decimal
from typing import Any

from trader.features.regime import blackout, killswitch, market_view, messages, regime
from trader.features.regime.settings import HedgeCfg, HedgeInstrument
from trader.kernel.registry.types import Port


class RegimePortImpl:
    """설정(`hedge`·`event_blackout`)과 원장 control 플래그로 답한다. 판정 로직은 기능 모듈에 있고
    여기서는 옮겨 부르기만 한다."""

    def __init__(self, settings: Any) -> None:
        self._settings = settings

    def hedge_cfg(self) -> HedgeCfg:
        return self._settings.hedge

    def state(self, conn: sqlite3.Connection, market: str) -> str:
        """국면(bull|neutral|bear). 헤지가 꺼져 있거나 판정 기록이 없으면 bull."""
        if not self.hedge_cfg().enabled:
            return "bull"
        r = regime.load(conn, market) or {}
        return str(r.get("state") or "bull")

    def load(self, conn: sqlite3.Connection, market: str) -> dict | None:
        return regime.load(conn, market)

    def core_count(self, state: dict | None) -> int:
        return regime.core_count(state)

    def view_today(self, conn: sqlite3.Connection, market: str, today: date) -> dict | None:
        """오늘 제출된 시장 판단(없거나 다른 날짜 것이면 None)."""
        return market_view.load_view(conn, market, today)

    def clamp_weight(self, view: dict | None) -> float:
        """제안 비중을 [min_weight_pct, max_weight_pct] 로 자른다. 제안이 없으면 max."""
        return market_view.clamp_weight(view, self.hedge_cfg())

    def hedge_tickers(self, market: str) -> frozenset[str]:
        cfg = self.hedge_cfg()
        return frozenset(cfg.tickers(market)) if cfg.enabled else frozenset()

    def instrument(self, market: str, *, leveraged: bool) -> HedgeInstrument | None:
        return self.hedge_cfg().instrument(market, leveraged=leveraged)

    def killswitch_reason(self, conn: sqlite3.Connection, account_id: str | None) -> str | None:
        return killswitch.reason(conn, account_id)

    def killswitch_observe(
        self,
        conn: sqlite3.Connection,
        *,
        account_id: str,
        market: str,
        nav: Decimal,
        today: date,
        limit_pct: float,
        positions: Any = None,
    ) -> bool:
        return killswitch.observe(
            conn,
            account_id=account_id,
            market=market,
            nav=nav,
            today=today,
            limit_pct=limit_pct,
            positions=positions,
        )

    def killswitch_message(self, conn: sqlite3.Connection, account_id: str) -> str | None:
        """이 계좌에 킬스위치가 켜져 있으면 알림 문구(`messages.killswitch_on`), 아니면 None.
        레인은 `killswitch_observe` 가 True(이번에 새로 켬)일 때만 한 번 보낸다(Ruling P11) — 레인이
        regime 을 import 하지 않도록 문구를 포트가 만든다(커널 Protocol 밖, 덕 타이핑)."""
        why = killswitch.reason(conn, account_id)
        return messages.killswitch_on(why) if why else None

    def blackout_reason(self, conn: sqlite3.Connection, ticker: str, today: date) -> str | None:
        return blackout.reason(conn, ticker, today, self._settings.event_blackout)


def build_port(boot: Any) -> RegimePortImpl:
    return RegimePortImpl(boot.settings)


PORT = Port("regime", build_port)
