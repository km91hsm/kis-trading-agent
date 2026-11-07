"""공식 KIS 순위 어댑터. 수집 범위는 제한된 API 페이지이며 원천 세션은 미확인이다."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation

from pydantic import BaseModel, ConfigDict

from trader.kernel.broker._parse import first_of, rows
from trader.kernel.broker.errors import KisApiError
from trader.kernel.broker.tr_ids import path


class ActivityRow(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ticker: str
    name: str | None = None
    market: str
    exchange: str
    currency: str
    source_rank: int | None = None
    price: Decimal | None = None
    change_pct: float | None = None
    volume: int | None = None
    turnover: Decimal | None = None
    source_asof: str | None = None
    basis: str = "unknown"


@dataclass
class ActivityPage:
    rows: list[ActivityRow] = field(default_factory=list)
    pages: int = 0
    truncated: bool = False
    error: str | None = None


def finite_decimal(value) -> Decimal | None:
    if value is None or str(value).strip() == "":
        return None
    try:
        d = Decimal(str(value).replace(",", ""))
    except (InvalidOperation, ValueError):
        return None
    return d if d.is_finite() else None


def _integer(value) -> int | None:
    d = finite_decimal(value)
    return int(d) if d is not None and d == d.to_integral_value() else None


def _parse(raw: dict, market: str, exchange: str) -> ActivityRow | None:
    us = market == "US"
    code = str(
        (first_of(raw, "symb") if us else first_of(raw, "mksc_shrn_iscd", "stck_shrn_iscd")) or ""
    ).strip()
    if not code or not re.fullmatch(r"[A-Za-z0-9.\-]{1,16}", code):
        return None
    pct = finite_decimal(raw.get("rate" if us else "prdy_ctrt"))
    pct_float = float(pct / 100) if pct is not None else None
    # Decimal can be finite while its float conversion overflows.
    if pct_float is not None and not (-float("inf") < pct_float < float("inf")):
        pct_float = None
    return ActivityRow(
        ticker=code,
        name=first_of(raw, "name", "hts_kor_isnm"),
        market=market,
        exchange=exchange,
        currency="USD" if us else "KRW",
        source_rank=_integer(first_of(raw, "rank", "data_rank")),
        price=finite_decimal(raw.get("last" if us else "stck_prpr")),
        change_pct=pct_float,
        volume=_integer(raw.get("tvol" if us else "acml_vol")),
        turnover=finite_decimal(raw.get("tamt" if us else "acml_tr_pbmn")),
    )


class ActivityMarketMixin:
    def activity_rank(
        self,
        market: str,
        kind: str,
        *,
        exchange: str | None = None,
        max_pages: int = 2,
        limit: int = 100,
    ) -> ActivityPage:
        """같은 KisClient의 인증/공유 limiter 사용. 페이지 실패도 이전 페이지를 보존한다."""
        if market not in {"KR", "US"} or kind not in {"turnover", "gain", "loss"}:
            raise ValueError("지원하지 않는 시장/순위입니다")
        max_pages, limit = min(max(max_pages, 1), 5), min(max(limit, 1), 200)
        if market == "US":
            if exchange not in {"NAS", "NYS", "AMS"}:
                raise ValueError("미국 순위 거래소는 NAS/NYS/AMS여야 합니다")
            tr = "OVS_TURNOVER_RANK" if kind == "turnover" else "OVS_CHANGE_RANK"
            params = {"EXCD": exchange, "NDAY": "0", "VOL_RANG": "0", "AUTH": "", "KEYB": ""}
            if kind == "turnover":
                params.update(PRC1="", PRC2="")
            else:
                params["GUBN"] = "1" if kind == "gain" else "0"
        else:
            exchange = "KRX"
            tr = "VOLUME_RANK" if kind == "turnover" else "FLUCT_RANK"
            params = dict(self._RANK_PARAMS[tr])
            if kind == "turnover":
                params["FID_BLNG_CLS_CODE"] = "3"
            else:
                params.update(
                    fid_rank_sort_cls_code="0" if kind == "gain" else "1",
                    fid_input_cnt_1=str(limit),
                )
        result = ActivityPage()
        seen: set[str] = set()
        seen_keys: set[str] = set()
        extra = None
        for _ in range(max_pages):
            try:
                data, headers = self._client._request_full(
                    "GET", path(tr), tr, params=params, extra_headers=extra
                )
            except KisApiError as e:
                if not result.pages:
                    raise
                result.error = f"{type(e).__name__}:{e.msg_cd}"
                result.truncated = True
                break
            result.pages += 1
            raw_rows = rows(data.get("output2" if market == "US" else "output"))
            added = 0
            for raw in raw_rows:
                row = _parse(raw, market, exchange)
                if row is not None and row.ticker not in seen:
                    seen.add(row.ticker)
                    result.rows.append(row)
                    added += 1
                    if len(result.rows) >= limit:
                        break
            more = headers.get("tr_cont") in {"M", "F"}
            result.truncated = more or len(raw_rows) > added
            if not more or len(result.rows) >= limit or not added:
                break
            # Domestic ranking has no documented cursor: do not repeat an unbounded page.
            if market == "KR":
                break
            key = str(data.get("keyb") or "")
            if not key or key in seen_keys:
                break
            seen_keys.add(key)
            params["KEYB"] = key
            extra = {"tr_cont": "N"}
        return result
