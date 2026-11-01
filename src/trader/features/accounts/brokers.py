"""계좌별 브로커 조립: 시세 키 선택, 같은 키 경고, 키 없는 계좌 건너뛰기.

시크릿 값은 로그에 남기지 않는다(접두사·계좌 id 만).
"""

from __future__ import annotations

import logging

from trader.features.accounts.settings import Account, load_accounts, to_broker_account
from trader.kernel.broker import factory
from trader.kernel.broker.broker import KisBroker
from trader.kernel.broker.creds import KisCreds
from trader.kernel.broker.fo import KisFoBroker
from trader.kernel.broker.market import KisMarket
from trader.kernel.broker.models import FoProduct
from trader.kernel.config import ConfigError
from trader.kernel.config.secrets import Secrets, account_prefix

logger = logging.getLogger("trader.accounts")


def market_creds(secrets: Secrets, accounts: list[Account] | None = None) -> KisCreds:
    """시세용 실전 키: 공용 `KIS_REAL_*` 가 있으면 그것, 없으면 첫 실전 **현물** 계좌의 전용 키
    (파생 계좌 키는 현물 시세 권한이 다를 수 있어 폴백에 쓰지 않는다)."""
    shared = secrets.kis_creds(env="real")
    if shared.app_key:
        return shared
    if accounts is None:
        try:
            accounts = load_accounts(secrets=secrets)
        except ConfigError:
            return shared
    for a in accounts:
        if a.env == "real" and a.asset == "stock":
            c = secrets.kis_creds(a)
            if c.app_key:
                return c
    return shared


def build_market(secrets: Secrets, accounts: list[Account] | None = None) -> KisMarket:
    """시세 클라이언트 (실전 env 전용)."""
    return factory.build_market(market_creds(secrets, accounts))


def build_brokers(
    secrets: Secrets,
    accounts: list[Account],
    *,
    fo_product: FoProduct | None = None,
) -> list[KisBroker | KisFoBroker]:
    """계좌별 브로커. 앱키가 없는 계좌, 상품 설정이 없는 파생 계좌는 경고 후 건너뛴다."""
    built: list[KisBroker | KisFoBroker] = []
    owners: dict[tuple[str, str], str] = {}  # (env, 접두사) → 계좌 id
    for a in accounts:
        creds = secrets.kis_creds(a)
        other = owners.setdefault((a.env, creds.prefix), a.id)
        if other != a.id:
            # KIS 는 앱키를 계좌마다 발급한다 — 같은 키로 다른 계좌를 부르면 거부될 수 있다.
            # 파생 계좌의 공용 키 폴백은 설계상 허용이라 INFO 로만 남긴다
            level = logging.INFO if a.asset == "fo" else logging.WARNING
            logger.log(
                level,
                "계좌 %s 가 %s 와 같은 앱키(%s*)를 씁니다 — 계좌 전용 키(%s*)를 넣으세요",
                a.id,
                other,
                creds.prefix,
                account_prefix(a.id, a.credentials),
            )
        if not creds.app_key or not creds.app_secret:
            logger.warning(
                "계좌 %s 건너뜀: %s 앱키·시크릿(%sAPP_KEY/SECRET)이 secrets/.env 에 없습니다",
                a.id,
                a.env,
                creds.prefix,
            )
            continue
        if a.asset == "fo" and fo_product is None:
            logger.warning(
                "계좌 %s 건너뜀: 파생 상품 설정(settings.fo)이 없습니다 — M10 에서 연결", a.id
            )
            continue
        built.append(factory.build_broker(to_broker_account(a), creds, fo_product=fo_product))
    return built
