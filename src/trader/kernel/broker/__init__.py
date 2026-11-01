"""KIS 브로커 계층(커널): 오류·TR 표·도메인 모델·호가단위·자격 DTO.

이후 Task 가 클라이언트·시세·주문 모듈을 덧붙인다.
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
    RETRY_CODE,
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
    "SHARED_DIR_ENV",
    "Check",
    "build_broker",
    "build_market",
    "client_for",
    "failures",
    "reset_clients",
    "run_checks",
    "shared_state_dir",
    "CONTRACT_VERSION",
    "FakeBroker",
    "FakeFoBroker",
    "FoMarketMixin",
    "KisFoBroker",
    "FakeMarket",
    "APPROVAL_TTL",
    "HOLIDAY_CACHE_DAYS",
    "PAGE_SIZE",
    "MAX_PAGES",
    "RATE_PER_SEC",
    "RETRY_CODE",
    "RETRY_MAX",
    "RETRY_WAIT_S",
    "TOKEN_ERROR_CODES",
    "FAIL_BACKOFF",
    "REFRESH_MARGIN",
    "UNKNOWN_RESPONSE_CODE",
    "US_EXCHANGE_CODE",
    "US_EXCHANGE_CODES",
    "US_ORDER_EXCHANGE",
    "US_QUOTE_EXCHANGE",
    "KisApiError",
    "KisAuth",
    "KisBroker",
    "KisClient",
    "KisCreds",
    "KisMarket",
    "RateLimitBusy",
    "RateLimiter",
    "SharedRateLimiter",
    "disabled_status",
    "is_on_tick",
    "is_on_tick_table",
    "key_fingerprint",
    "quantize_price",
    "quantize_tick",
    "tick_for",
    "tick_size",
    "tick_table_for",
    "tr_ids",
]
