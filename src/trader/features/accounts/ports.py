"""accounts 포트: 레인이 `Boot.ports["accounts"]` 로 받는 계좌·브로커·시세·모드 구현체."""

from __future__ import annotations

import logging
import sqlite3
from collections.abc import Callable
from typing import Any

from trader.features.accounts import api
from trader.features.accounts.api import Account
from trader.kernel.config.secrets import Secrets
from trader.kernel.registry.types import Port

logger = logging.getLogger("trader.accounts")


class AccountsPortImpl:
    """계좌 목록과 그로부터 조립한 브로커·시세. 모드·일시정지는 원장 조회."""

    def __init__(self, accounts: list[Account], secrets: Secrets) -> None:
        self._accounts = accounts
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
        """(실효 모드, 설명). 실전 auto 인데 LIVE 미확인이면 off."""
        return api.effective_mode(conn, account)

    def paused(self, conn: sqlite3.Connection) -> bool:
        return api.is_paused(conn)

    def holiday_fn(self) -> Callable[..., bool] | None:
        return None  # M5


PORT = Port("accounts", lambda b: AccountsPortImpl(api.load_accounts(secrets=b.secrets), b.secrets))
