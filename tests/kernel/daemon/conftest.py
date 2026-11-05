"""데몬 테스트 공용 헬퍼."""

from typing import Any


class FakeAccountsPort:
    """`ports["accounts"]` 자리의 가짜: 합성 계좌·브로커·시세를 그대로 돌려준다(네트워크 없음).
    `market` 을 바꾸면 다음 `market_client()` 부터 새 시세가 나온다."""

    def __init__(
        self,
        *,
        market_client: Any = None,
        brokers: dict[str, Any] | None = None,
        accounts: list | None = None,
    ) -> None:
        self.market = market_client
        self._brokers = dict(brokers or {})
        self._accounts = list(accounts or [])

    def accounts(self) -> list:
        return list(self._accounts)

    def brokers(self) -> list[Any]:
        return list(self._brokers.values())

    def market_client(self) -> Any:
        return self.market
