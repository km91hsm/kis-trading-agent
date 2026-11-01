"""국내선물옵션 시세 믹스인(`KisMarket` 이 상속). 실전·모의 같은 tr_id, 시세는 실전 클라이언트만.

`self` 는 KisMarket(`_client`·`_now`·`_levels` 를 가진다). market.py 를 import 하지 않는다."""

from __future__ import annotations

import logging
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from trader.kernel.broker._parse import dec as _dec
from trader.kernel.broker._parse import first_of as _first
from trader.kernel.broker._parse import rows as _rows
from trader.kernel.broker._parse import to_int as _int
from trader.kernel.broker.errors import KisApiError
from trader.kernel.broker.models import Candle, FoKind, FoQuote, OrderBook
from trader.kernel.broker.tr_ids import path
from trader.kernel.config.time import KST

logger = logging.getLogger("trader.broker")


def _ymd(s: str) -> date:
    return date(int(s[:4]), int(s[4:6]), int(s[6:8]))


FO_DIV = {"FUTURES": "F", "CALL": "O", "PUT": "O"}
OPTION_CHAIN_MAX_CALLS = 18


def _hms_to_dt(hms: object, base: datetime) -> datetime | None:
    """응답의 'HHMMSS' 시각을 base(KST 날짜) 의 시각으로. 없으면 None(수신시각으로 대체하지
    않는다)."""
    s = str(hms or "").strip()
    if len(s) != 6 or not s.isdigit():
        return None
    local = base.astimezone(KST).replace(
        hour=int(s[:2]), minute=int(s[2:4]), second=int(s[4:6]), microsecond=0
    )
    return local.astimezone(UTC)


def _opt_float(v: object) -> float | None:
    if v in (None, ""):
        return None
    try:
        return float(_dec(v))
    except Exception:  # noqa: BLE001
        return None


def _opt_dec(v: object) -> Decimal | None:
    return None if v in (None, "") else _dec(v)


def _kospi200_block(data: dict) -> dict:
    """시세 응답의 코스피200 묶음. 실기 1(2026-10-28): output2 는 코스피 종합(업종 0001), output3 이
    코스피200(2001) — 업종코드로 고르고, 코드가 붙은 묶음 중 2001 이 없으면 빈 묶음(기초지수
    없음 → 게이트가 막는다). 코드가 없는 옛 응답만 output2 를 그대로 쓴다."""
    blocks = [data.get(k) or {} for k in ("output3", "output2")]
    coded = [b for b in blocks if str(b.get("bstp_cls_code") or "").strip()]
    if coded:
        return next((b for b in coded if str(b["bstp_cls_code"]).strip() == "2001"), {})
    return data.get("output2") or {}


def _book_out(data: dict) -> dict:
    """호가 응답의 호가 묶음 — 실기로 확인한 output2, 없으면 옛 가정 output1."""
    out2 = data.get("output2") or {}
    return out2 if out2.get("futs_askp1") or out2.get("futs_bidp1") else (data.get("output1") or {})


def _fo_book_top(
    self, code: str, kind: FoKind, now: datetime
) -> tuple[Decimal | None, Decimal | None, datetime | None]:
    """(매수 1호가, 매도 1호가, 호가접수시간). 조회 실패는 (None, None, None)."""
    try:
        data = self._client.get(
            path("FO_ORDERBOOK"),
            "FO_ORDERBOOK",
            {"FID_COND_MRKT_DIV_CODE": FO_DIV[kind], "FID_INPUT_ISCD": code},
        )
    except KisApiError as e:
        logger.warning("파생 호가 조회 실패 %s(시각·1호가 없음): %s", code, e)
        return None, None, None
    b = _book_out(data)
    return (
        _opt_dec(b.get("futs_bidp1")),
        _opt_dec(b.get("futs_askp1")),
        _hms_to_dt(b.get("aspr_acpt_hour"), now),
    )


class FoMarketMixin:
    """`get_fo_*`·`get_option_chain`·`get_kospi200` 를 KisMarket 에 더한다."""

    def get_fo_price(self, code: str, kind: FoKind) -> FoQuote:
        """선물옵션 시세. output1 선물/옵션 필드 + output2 기초지수(코스피200). 옵션은 그릭·IV·
        행사가."""
        data = self._client.get(
            path("FO_PRICE"),
            "FO_PRICE",
            {"FID_COND_MRKT_DIV_CODE": FO_DIV[kind], "FID_INPUT_ISCD": code},
        )
        out = data.get("output1") or {}
        idx = _kospi200_block(data)
        now = self._now()
        ltd_raw = str(out.get("futs_last_tr_date") or "").strip()
        days_raw = out.get("hts_rmnn_dynu")
        under_raw = idx.get("bstp_nmix_prpr")
        bid = _opt_dec(_first(out, "futs_bidp", "bidp1", "futs_bidp1"))
        ask = _opt_dec(_first(out, "futs_askp", "askp1", "futs_askp1"))
        asof = _hms_to_dt(_first(out, "futs_cntg_hour", "stck_cntg_hour", "bsop_hour"), now)
        if bid is None or ask is None or asof is None:
            # 실기 1(2026-10-28): 시세 응답에는 시각·1호가가 없다 → 호가 응답 output2 의 1호가와
            # 호가접수시간(시장 시각). 실패하면 None 그대로(진입 게이트가 막는다, 수신시각
            # 대체 없음)
            book = _fo_book_top(self, code, kind, now)
            bid = bid if bid is not None else book[0]
            ask = ask if ask is not None else book[1]
            asof = asof if asof is not None else book[2]
        return FoQuote(
            code=code,
            kind=kind,
            price=_dec(out.get("futs_prpr")),
            bid=bid,
            ask=ask,
            change_pct=float(_dec(out.get("futs_prdy_ctrt"))) / 100,
            volume=_int(out.get("acml_vol")),
            open_interest=_int(out["hts_otst_stpl_qty"]) if out.get("hts_otst_stpl_qty") else None,
            basis=_opt_dec(out.get("basis")) if kind == "FUTURES" else None,
            upper=_opt_dec(out.get("futs_mxpr")),
            lower=_opt_dec(out.get("futs_llam")),
            last_trade_date=_ymd(ltd_raw) if len(ltd_raw) == 8 else None,
            days_left=_int(days_raw) if days_raw not in (None, "") else None,
            hist_vol=_opt_float(out.get("hist_vltl")),
            iv=_opt_float(out.get("hts_ints_vltl")) if kind != "FUTURES" else None,
            delta=_opt_float(out.get("delta_val")) if kind != "FUTURES" else None,
            gamma=_opt_float(out.get("gama")) if kind != "FUTURES" else None,
            theta=_opt_float(out.get("theta")) if kind != "FUTURES" else None,
            vega=_opt_float(out.get("vega")) if kind != "FUTURES" else None,
            strike=_opt_dec(out.get("acpr")) if kind != "FUTURES" else None,
            underlying=_opt_dec(under_raw),
            underlying_asof=_hms_to_dt(_first(idx, "bsop_hour", "stck_cntg_hour"), now),
            quote_asof=asof,
            received_at=now,
        )

    def get_fo_orderbook(self, code: str, kind: FoKind) -> OrderBook:
        data = self._client.get(
            path("FO_ORDERBOOK"),
            "FO_ORDERBOOK",
            {"FID_COND_MRKT_DIV_CODE": FO_DIV[kind], "FID_INPUT_ISCD": code},
        )
        out = _book_out(data)
        now = self._now()
        return OrderBook(
            ticker=code,
            market="KR",
            asks=self._levels(out, "futs_askp", "askp_rsqn"),
            bids=self._levels(out, "futs_bidp", "bidp_rsqn"),
            asof=_hms_to_dt(out.get("aspr_acpt_hour"), now) or now,
        )

    def get_fo_daily(self, code: str, kind: FoKind, *, days: int = 100) -> list[Candle]:
        """일봉(날짜 오름차순). 한 호출(최근 days 거래일 창)만 — 파생은 월물 수명이 짧다."""
        end = self._now().astimezone(KST).date()
        start = end - timedelta(days=int(days * 1.6) + 7)
        data = self._client.get(
            path("FO_DAILY"),
            "FO_DAILY",
            {
                "FID_COND_MRKT_DIV_CODE": FO_DIV[kind],
                "FID_INPUT_ISCD": code,
                "FID_INPUT_DATE_1": start.strftime("%Y%m%d"),
                "FID_INPUT_DATE_2": end.strftime("%Y%m%d"),
                "FID_PERIOD_DIV_CODE": "D",
            },
        )
        out = [
            Candle(
                date=_ymd(str(r["stck_bsop_date"])),
                open=_dec(r.get("futs_oprc")),
                high=_dec(r.get("futs_hgpr")),
                low=_dec(r.get("futs_lwpr")),
                close=_dec(r.get("futs_prpr")),
                volume=_int(r.get("acml_vol")),
            )
            for r in _rows(data.get("output2"))
            if r.get("stck_bsop_date")
        ]
        out.sort(key=lambda c: c.date)
        return out[-days:]

    def get_fo_minute(self, code: str, kind: FoKind, *, count: int = 60) -> list[Candle]:
        """당일 1분봉(시간 오름차순, 체결 시각 빈 행 제외). 한 호출(최대 102건)."""
        local = self._now().astimezone(KST)
        data = self._client.get(
            path("FO_MINUTE"),
            "FO_MINUTE",
            {
                "FID_COND_MRKT_DIV_CODE": FO_DIV[kind],
                "FID_INPUT_ISCD": code,
                "FID_HOUR_CLS_CODE": "60",
                "FID_PW_DATA_INCU_YN": "N",
                "FID_FAKE_TICK_INCU_YN": "N",
                "FID_INPUT_DATE_1": local.strftime("%Y%m%d"),
                "FID_INPUT_HOUR_1": local.strftime("%H%M%S"),
            },
        )
        out = [
            Candle(
                date=_ymd(str(r["stck_bsop_date"])),
                open=_dec(r.get("futs_oprc")),
                high=_dec(r.get("futs_hgpr")),
                low=_dec(r.get("futs_lwpr")),
                close=_dec(r.get("futs_prpr")),
                volume=_int(r.get("cntg_vol")),
                time=str(r["stck_cntg_hour"]),
            )
            for r in _rows(data.get("output2"))
            if r.get("stck_bsop_date") and r.get("stck_cntg_hour")
        ]
        out.sort(key=lambda c: (c.date, c.time or ""))
        return out[-count:]

    def get_option_chain(
        self, contracts: list, *, strikes: list[Decimal]
    ) -> tuple[list[FoQuote], list[str]]:
        """행사가 목록에 드는 옵션 계약의 시세(≤ OPTION_CHAIN_MAX_CALLS 호출). 실패 행은 건너뛰고
        코드를 errors 로 돌려준다."""
        wanted = set(strikes)
        targets = [c for c in contracts if c.strike is not None and c.strike in wanted]
        targets = targets[:OPTION_CHAIN_MAX_CALLS]
        quotes: list[FoQuote] = []
        errors: list[str] = []
        for c in targets:
            try:
                quotes.append(self.get_fo_price(c.code, c.kind))
            except KisApiError as e:
                logger.warning("옵션 체인 시세 실패 %s: %s", c.code, e)
                errors.append(c.code)
        return quotes, errors

    def get_kospi200(self) -> tuple[Decimal, datetime | None]:
        """코스피200 현재값과 응답의 시장 시각(없으면 None — 수신시각으로 대체하지 않는다)."""
        data = self._client.get(
            path("DOM_INDEX"),
            "DOM_INDEX",
            {"FID_COND_MRKT_DIV_CODE": "U", "FID_INPUT_ISCD": "2001"},
        )
        out = data.get("output") or {}
        return _dec(out.get("bstp_nmix_prpr")), _hms_to_dt(
            _first(out, "bsop_hour", "stck_cntg_hour"), self._now()
        )
