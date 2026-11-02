"""execution 코어가 기능에 기대는 접점(Protocol 만). 구현은 accounts·ingest 기능이 맡는다."""

from __future__ import annotations

from collections.abc import Callable
from datetime import date, datetime
from typing import Any, Protocol


class AccountsPort(Protocol):
    """계좌 목록·브로커·시세·정책 조회."""

    def accounts(self) -> list[Any]: ...

    def brokers(self) -> dict[str, Any]: ...

    def market_client(self) -> Any | None: ...

    def policy(self, conn: Any, account: Any) -> tuple[str, str | None]:
        """계좌의 유효 모드와 사유(effective_mode)."""
        ...

    def paused(self, conn: Any) -> bool: ...

    def holiday_fn(self) -> Callable[[str, date], bool] | None: ...


class InputsPort(Protocol):
    """종목 입력(공시·뉴스·거시 등) 수집."""

    def collect(
        self,
        *,
        ticker: str,
        market: str,
        exchange: str | None,
        asof: datetime | None,
        force: bool,
        conn_factory: Callable[[], Any],
    ) -> dict:
        """`00_inputs` 를 저장하고 그 payload 를 돌려준다."""
        ...
