"""KIS 브로커 계층(커널): 인증·클라이언트·시세·주문/잔고·파생·웹소켓 프레임·가짜 구현·점검.

도메인 모델·오류·TR 표·호가단위·자격 DTO 와 함께 `client_for`/`build_market`/`build_broker` 로
조립한다. 공개 이름은 아래 `__all__` 이 정본이다.
"""

from __future__ import annotations

from trader.kernel.broker import models, tr_ids
from trader.kernel.broker.auth import APPROVAL_TTL, FAIL_BACKOFF, REFRESH_MARGIN, KisAuth
from trader.kernel.broker.broker import (
    US_EXCHANGE_CODE,
    US_EXCHANGE_CODES,
    US_ORDER_EXCHANGE,
    US_QUOTE_EXCHANGE,
    KisBroker,
)
from trader.kernel.broker.client import (
    MAX_PAGES,
    RATE_PER_SEC,
    RETRY_CODES,
    RETRY_MAX,
    RETRY_WAIT_S,
    TOKEN_ERROR_CODES,
    KisClient,
    RateLimitBusy,
    RateLimiter,
)
from trader.kernel.broker.creds import KisCreds, key_fingerprint
from trader.kernel.broker.errors import UNKNOWN_RESPONSE_CODE, KisApiError
from trader.kernel.broker.factory import (
    SHARED_DIR_ENV,
    build_broker,
    build_market,
    client_for,
    reset_clients,
    shared_state_dir,
)
from trader.kernel.broker.fake import FakeBroker, FakeFoBroker, FakeMarket
from trader.kernel.broker.fo import CONTRACT_VERSION, KisFoBroker
from trader.kernel.broker.market import HOLIDAY_CACHE_DAYS, PAGE_SIZE, KisMarket
from trader.kernel.broker.market_fo import FoMarketMixin
from trader.kernel.broker.models import *  # noqa: F403
from trader.kernel.broker.ping import Check, failures, run_checks
from trader.kernel.broker.rate_store import SharedRateLimiter, disabled_status
from trader.kernel.broker.ticks import (
    is_on_tick,
    is_on_tick_table,
    quantize_price,
    quantize_tick,
    tick_for,
    tick_size,
    tick_table_for,
)
from trader.kernel.broker.ws import (
    BOOK_LEVELS,
    INDEX_CODES,
    MAX_SUBSCRIPTIONS,
    TR_BOOK,
    TR_INDEX,
    TR_NOTICE,
    TR_PINGPONG,
    TR_TRADE,
    TRADE_FIELD_COUNT,
    TRADE_FIELDS,
    control_problem,
    is_appkey_in_use,
    is_notice,
    is_pingpong,
    parse_frame,
    subscribe_message,
)

__all__ = [
    *models.__all__,
    # 오류·자격·TR 표
    "UNKNOWN_RESPONSE_CODE",
    "KisApiError",
    "KisCreds",
    "key_fingerprint",
    "tr_ids",
    # 인증·클라이언트·레이트
    "APPROVAL_TTL",
    "FAIL_BACKOFF",
    "REFRESH_MARGIN",
    "KisAuth",
    "MAX_PAGES",
    "RATE_PER_SEC",
    "RETRY_CODES",
    "RETRY_MAX",
    "RETRY_WAIT_S",
    "TOKEN_ERROR_CODES",
    "KisClient",
    "RateLimitBusy",
    "RateLimiter",
    "SharedRateLimiter",
    "disabled_status",
    # 시세·주문·파생
    "HOLIDAY_CACHE_DAYS",
    "PAGE_SIZE",
    "KisMarket",
    "FoMarketMixin",
    "US_EXCHANGE_CODE",
    "US_EXCHANGE_CODES",
    "US_ORDER_EXCHANGE",
    "US_QUOTE_EXCHANGE",
    "KisBroker",
    "CONTRACT_VERSION",
    "KisFoBroker",
    # 호가단위
    "is_on_tick",
    "is_on_tick_table",
    "quantize_price",
    "quantize_tick",
    "tick_for",
    "tick_size",
    "tick_table_for",
    # 웹소켓 프레임
    "BOOK_LEVELS",
    "INDEX_CODES",
    "MAX_SUBSCRIPTIONS",
    "TR_BOOK",
    "TR_INDEX",
    "TR_NOTICE",
    "TR_PINGPONG",
    "TR_TRADE",
    "TRADE_FIELD_COUNT",
    "TRADE_FIELDS",
    "control_problem",
    "is_appkey_in_use",
    "is_notice",
    "is_pingpong",
    "parse_frame",
    "subscribe_message",
    # 가짜 구현
    "FakeBroker",
    "FakeFoBroker",
    "FakeMarket",
    # 조립·점검
    "SHARED_DIR_ENV",
    "build_broker",
    "build_market",
    "client_for",
    "reset_clients",
    "shared_state_dir",
    "Check",
    "failures",
    "run_checks",
]
