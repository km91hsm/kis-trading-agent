"""accounts 포트: 레인이 `Boot.ports["accounts"]` 로 받는 계좌·브로커·시세·모드 구현체."""

from __future__ import annotations

import logging
import sqlite3
from collections.abc import Callable
from datetime import date
from typing import Any

from trader.features.accounts import api
from trader.features.accounts.api import Account
from trader.kernel.config import market_hours
from trader.kernel.config.secrets import Secrets
from trader.kernel.registry.types import Port

logger = logging.getLogger("trader.accounts")


class AccountsPortImpl:
    """현물 계좌 목록과 그로부터 조립한 브로커·시세. 모드·일시정지는 원장 조회.

    이 포트는 현물 레인용이라 파생(fo) 계좌는 받는 즉시 걸러 낸다 — 계좌·브로커·시세가 모두
    같은 현물 목록에서 나온다."""

    def __init__(self, accounts: list[Account], secrets: Secrets) -> None:
        self._accounts = api.split_accounts(accounts)[0]
        self._secrets = secrets

    def accounts(self) -> list[Account]:
        return list(self._accounts)

    def brokers(self) -> list[Any]:
        """키 없는 계좌는 경고 후 제외한다(`api.build_brokers` 규칙)."""
        return api.build_brokers(self._secrets, self._accounts)

    def market_client(self) -> Any | None:
        """시세 클라이언트. 시세 키가 없어 만들 수 없으면 None."""
        try:
            return api.build_market(self._secrets, self._accounts)
        except Exception as e:  # noqa: BLE001 - 시세 없이도 레인은 돌아야 한다
            logger.warning("시세 클라이언트를 만들지 못함: %s", type(e).__name__)
            return None

    def policy(self, conn: sqlite3.Connection, account: Account) -> tuple[str, str | None]:
        """(주문 경로 모드, 설명). 실전 파생은 항상 off, 실전 auto 인데 LIVE 미확인이면 off."""
        return api.account_mode(conn, account)

    def paused(self, conn: sqlite3.Connection) -> bool:
        return api.is_paused(conn)

    def holiday_fn(self) -> Callable[[str, date], bool]:
        """`(market, d) -> bool` 휴장 판정. KR 은 KIS 조회(실패 = 개장), US 는 NYSE 규칙."""
        client = self.market_client()

        def is_holiday(market: str, d: date) -> bool:
            if market == "US":
                return market_hours.is_us_holiday(d)
            if client is None:
                return False
            try:
                return bool(client.is_holiday(d))
            except Exception as e:  # noqa: BLE001 - 조회 실패는 개장으로 본다
                logger.warning("휴장일 조회 실패, 개장으로 처리: %s", type(e).__name__)
                return False

        return is_holiday


PORT = Port("accounts", lambda b: AccountsPortImpl(api.load_accounts(secrets=b.secrets), b.secrets))
