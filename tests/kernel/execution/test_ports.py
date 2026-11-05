"""커널 포트 Protocol 이 실제 구현과 같은 모양인지 — 가짜가 Protocol 과 어긋나지 않게 하는 기준."""

import typing

from tests.features.spot.approval_helpers import make_account
from trader.features.accounts.ports import AccountsPortImpl
from trader.kernel.config.secrets import Secrets
from trader.kernel.execution import ports


def test_accounts_port_brokers_is_list():
    """`AccountsPort.brokers` 는 list(각 `.account.id` 로 계좌를 안다) — dict 가 아니다."""
    hints = typing.get_type_hints(ports.AccountsPort.brokers)
    assert typing.get_origin(hints["return"]) is list
    real = AccountsPortImpl([make_account("paper-main", "paper", "off")], Secrets())
    assert isinstance(real.brokers(), list)  # 키 없는 계좌는 빠져도 모양은 list
