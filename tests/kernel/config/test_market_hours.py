from datetime import UTC, date, datetime, time

from trader.kernel.config.market_hours import (
    in_window,
    is_open,
    is_us_holiday,
    market_close,
    us_early_closes,
    us_holidays,
)


def test_kr_regular_session():
    assert is_open("KR", datetime(2026, 10, 9, 6, 0, tzinfo=UTC))  # 금 15:00 KST
    assert not is_open("KR", datetime(2026, 10, 9, 7, 0, tzinfo=UTC))  # 16:00 KST
    assert not is_open("KR", datetime(2026, 10, 10, 2, 0, tzinfo=UTC))  # 토요일


def test_us_regular_session_and_holiday():
    assert is_open("US", datetime(2026, 10, 9, 14, 0, tzinfo=UTC))  # 금 10:00 ET
    assert not is_open("US", datetime(2026, 10, 9, 21, 0, tzinfo=UTC))  # 17:00 ET
    assert not is_open("US", datetime(2026, 12, 25, 15, 0, tzinfo=UTC))  # 성탄절


def test_us_early_close_ends_session_at_13_et():
    from datetime import UTC, datetime

    from trader.kernel.config import market_hours

    # 2026-11-27 (추수감사절 다음 날) 13:30 ET = 18:30 UTC
    t = datetime(2026, 11, 27, 18, 30, tzinfo=UTC)
    assert market_hours.close_hm("US", t.date()) == "13:00"
    assert not market_hours.is_open("US", t) and market_hours.session_over("US", t)
    normal = datetime(2026, 11, 25, 18, 30, tzinfo=UTC)
    assert market_hours.is_open("US", normal) and not market_hours.session_over("US", normal)


def test_completed_candles_drops_today_bar_until_session_over():
    """전체 검토 2차 2-P4(L7): 장중의 오늘 봉은 거래량이 있어도 미완성이라 버린다(마감 뒤 포함),
    미래 날짜·거래량 0 꼬리도 버린다."""
    from datetime import date
    from decimal import Decimal

    from trader.kernel.broker.models import Candle
    from trader.kernel.config.market_hours import completed_candles

    def c(d, vol=10):
        one = Decimal(1)
        return Candle(date=d, open=one, high=one, low=one, close=one, volume=vol)

    cs = [c(date(2026, 10, 8)), c(date(2026, 10, 9))]
    assert completed_candles("KR", cs, datetime(2026, 10, 9, 1, 0, tzinfo=UTC)) == cs[:1]  # 10:00
    assert completed_candles("KR", cs, datetime(2026, 10, 9, 7, 0, tzinfo=UTC)) == cs  # 16:00
    assert completed_candles("US", cs, datetime(2026, 10, 9, 14, 0, tzinfo=UTC)) == cs[:1]
    assert completed_candles("US", cs, datetime(2026, 10, 9, 21, 0, tzinfo=UTC)) == cs
    future = cs + [c(date(2026, 10, 12))]
    assert completed_candles("KR", future, datetime(2026, 10, 9, 7, 0, tzinfo=UTC)) == cs
    stub = cs + [c(date(2026, 10, 12), vol=0)]
    assert completed_candles("KR", stub, datetime(2026, 10, 12, 7, 0, tzinfo=UTC)) == cs


def test_in_window_handles_midnight_crossing():
    assert in_window(time(10, 0), "09:00", "15:30")
    assert not in_window(time(15, 31), "09:00", "15:30")
    assert in_window(time(23, 45), "23:30", "06:00")
    assert in_window(time(2, 0), "23:30", "06:00")
    assert not in_window(time(12, 0), "23:30", "06:00")


def test_us_holidays_rules_match_static_and_2028():
    assert us_holidays(2026) == {
        date(2026, 1, 1), date(2026, 1, 19), date(2026, 2, 16), date(2026, 4, 3),
        date(2026, 5, 25), date(2026, 6, 19), date(2026, 7, 3), date(2026, 9, 7),
        date(2026, 11, 26), date(2026, 12, 25),
    }  # fmt: skip
    assert us_holidays(2027) == {
        date(2027, 1, 1), date(2027, 1, 18), date(2027, 2, 15), date(2027, 3, 26),
        date(2027, 5, 31), date(2027, 6, 18), date(2027, 7, 5), date(2027, 9, 6),
        date(2027, 11, 25), date(2027, 12, 24),
    }  # fmt: skip
    h28 = us_holidays(2028)
    assert date(2028, 4, 14) in h28 and date(2028, 11, 23) in h28
    assert date(2027, 12, 31) not in h28 and date(2028, 1, 1) not in h28  # 토요일 신정: 대체 없음
    assert is_us_holiday(date(2028, 7, 4)) and not is_us_holiday(date(2028, 7, 5))
    # should_skip 의 US 분기(daemon)는 Task 5 — 여기서는 규칙 쪽 단언만 옮긴다
    assert date(2028, 4, 14) in us_holidays(2028)


def test_early_close_uses_exchange_time():
    """조기 마감(2026-11-27 13:00 ET)은 거래소 시각으로 마감한다. 옛 테스트의 daemon
    `_in_market_window`(스케줄 tz·큐 기계) 부분은 Task 5 로 넘기고 `market_close` 단언만 옮긴다."""
    assert market_close("US", date(2026, 11, 27)) == time(13, 0)
    assert market_close("US", date(2026, 11, 25)) == time(16, 0)
    assert market_close("KR", date(2026, 11, 27)) == time(15, 30)
    assert us_early_closes(2026) >= {date(2026, 11, 27), date(2026, 12, 24)}
