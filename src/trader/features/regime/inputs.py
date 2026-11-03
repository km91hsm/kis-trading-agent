"""국면 판정 입력: 지수 proxy 완성 일봉·US VIX(FRED)·KR 외인 수급 약세.

옛 데몬 `_proxy_candles`·`_vix`·`_flow_weak`. 시세 클라이언트·시크릿은 호출자가 넘긴다."""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import datetime
from typing import Any

import httpx

from trader.features.ingest import api as ingest
from trader.features.regime import regime as regime_mod
from trader.features.regime.settings import HedgeCfg
from trader.kernel.config import market_hours

logger = logging.getLogger("trader.regime")


def proxy_candles(
    market_client: Any, market: str, cfg: HedgeCfg, *, now: datetime, days: int = 260
) -> list:
    """`hedge.index_proxy` 종목 일봉. 시세 클라이언트·proxy 가 없으면 빈 목록.
    개장 전 거래량 0 스텁·장중의 미완성 오늘 봉이 들어가면 20일 수익률·변동성이 가짜 종가로
    계산된다 → 완성 봉까지만(2-P4)."""
    inst = cfg.index_proxy.get(market)  # type: ignore[call-overload]
    if inst is None or market_client is None:
        return []
    if market == "US":
        got = market_client.get_daily_us(inst.symbol, inst.exchange or "AMS", days=days)
    else:
        got = market_client.get_daily_kr(inst.symbol, days=days)
    return market_hours.completed_candles(market, got, now)


def vix(
    secrets: Any, market: str, *, http_factory: Callable[[], Any] | None = None
) -> float | None:
    """US 국면의 변동성 지표는 FRED VIX. 키가 없거나 실패하면 None → 실현변동성으로 대체."""
    key = getattr(secrets, "FRED_API_KEY", None) if secrets is not None else None
    if market != "US" or not key:
        return None
    try:
        with (http_factory or httpx.Client)() as http:
            return float(ingest.Fred(key, http).latest("VIXCLS")["value"])
    except Exception as e:  # noqa: BLE001 - VIX 는 보조 지표
        logger.warning("VIX 조회 실패, 실현변동성으로 대체: %s", e)
        return None


def flow_weak_signal(market_client: Any, market: str, cfg: HedgeCfg) -> bool:
    """Phase 21 보조 지표: KR 지수 proxy 외인 순매수 20일·5일 합이 모두 음수. `hedge.flow_signal`
    이 꺼져 있거나 KR 이 아니거나 조회에 실패하면 False(지표 없음)."""
    if not cfg.flow_signal or market != "KR" or market_client is None:
        return False
    inst = cfg.index_proxy.get(market)  # type: ignore[call-overload]
    if inst is None:
        return False
    try:
        flows = market_client.get_investor_kr(inst.symbol, days=20)
    except Exception as e:  # noqa: BLE001 - 보조 지표
        logger.warning("외인 수급 조회 실패 %s: %s", market, e)
        return False
    return regime_mod.flow_weak(flows)
