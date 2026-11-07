"""해외지수 일별 관측. 관측일과 값은 반드시 같은 일별 행에서 취한다."""

from __future__ import annotations

from datetime import date, datetime, timedelta

from trader.kernel.broker._parse import dec, rows
from trader.kernel.broker.client import KisClient
from trader.kernel.broker.tr_ids import path
from trader.kernel.config.time import MARKET_TZ


class IndexMarketMixin:
    _client: KisClient

    def get_index_us_daily(self, symbol: str, *, asof: datetime) -> dict | None:
        """SPX·COMP의 미국시장 전일 이하 일별값을 최대 14일 창에서 한 번 조회한다.

        당일 행은 과거 재수집 시 뒤늦은 최종값일 수 있어 제외한다. 일별 관측이며
        종가 확정 여부·발표시각·실시간성을 보증하지 않는다. 유효 행이 없으면 None.
        """
        if symbol not in {"SPX", "COMP"}:
            raise ValueError("지원하는 미국 지수는 SPX·COMP뿐입니다")
        if asof.tzinfo is None:
            raise ValueError("asof에는 timezone이 필요합니다")
        end = asof.astimezone(MARKET_TZ["US"]).date() - timedelta(days=1)
        start = end - timedelta(days=13)
        data = self._client.get(
            path("OVS_FX_DAILY"),
            "OVS_FX_DAILY",
            {
                "FID_COND_MRKT_DIV_CODE": "N",
                "FID_INPUT_ISCD": symbol,
                "FID_INPUT_DATE_1": start.strftime("%Y%m%d"),
                "FID_INPUT_DATE_2": end.strftime("%Y%m%d"),
                "FID_PERIOD_DIV_CODE": "D",
            },
        )
        valid = []
        for row in rows(data.get("output2")):
            raw = str(row.get("stck_bsop_date") or "").strip()
            if len(raw) != 8 or not raw.isascii() or not raw.isdigit():
                continue
            try:
                day = date(int(raw[:4]), int(raw[4:6]), int(raw[6:]))
            except ValueError:
                continue
            value = dec(row.get("ovrs_nmix_prpr"))
            if start <= day <= end and value.is_finite() and value > 0:
                valid.append((day, value))
        if not valid:
            return None
        day, value = max(valid, key=lambda item: item[0])
        return {"value": value, "date": day.isoformat(), "symbol": symbol}
