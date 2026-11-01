"""KIS 브로커 계층(커널): 오류·TR 표·도메인 모델·호가단위·자격 DTO.

이후 Task 가 클라이언트·시세·주문 모듈을 덧붙인다.
"""

from __future__ import annotations

from trader.kernel.broker import models, tr_ids
from trader.kernel.broker.auth import APPROVAL_TTL, FAIL_BACKOFF, REFRESH_MARGIN, KisAuth
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
from trader.kernel.broker.market import HOLIDAY_CACHE_DAYS, PAGE_SIZE, KisMarket
from trader.kernel.broker.models import *  # noqa: F403
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

__all__ = [
    *models.__all__,
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
    "KisApiError",
    "KisAuth",
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
