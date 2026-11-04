"""정규장 시계와 NYSE 휴장 규칙. KR 09:00~15:30 KST, US 09:30~16:00 ET, 평일만.

US 휴장·조기 마감은 규칙 계산(`us_holidays`·`us_early_closes`)을 쓰고, KR 휴장은 KIS 조회 없이는
모르므로 평일이면 연다(휴장일 시세 조회는 전일 종가가 돌아올 뿐 해가 없다). KR 휴장 조회는
`accounts` 포트의 `holiday_fn` 이 맡는다."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from typing import Any

from trader.kernel.broker.models import drop_incomplete_tail
from trader.kernel.config.time import MARKET_TZ

REGULAR = {"KR": ("09:00", "15:30"), "US": ("09:30", "16:00")}
MARKET_CLOSE: dict[str, time] = {"KR": time(15, 30), "US": time(16, 0)}  # 현지 정규장 마감
US_EARLY_CLOSE = "13:00"  # NYSE 조기 마감일(`us_early_closes`)
FO_SESSION = ("08:45", "15:45")  # KRX 파생 정규장(KST)
FO_LAST_DAY_CLOSE = "15:20"  # 최종거래일 마감(KST)


def _hm(s: str) -> time:
    h, m = s.split(":")
    return time(int(h), int(m))


def in_window(now_t: time, start: str, end: str) -> bool:
    """start ≤ now ≤ end. end 가 start 보다 이르면 자정을 넘는 창이다 (US 23:30~06:00)."""
    s, e = _hm(start), _hm(end)
    if s <= e:
        return s <= now_t <= e
    return now_t >= s or now_t <= e


def _easter(year: int) -> date:
    """부활절(그레고리력, Anonymous 알고리즘)."""
    a, b, c = year % 19, year // 100, year % 100
    d, e = b // 4, b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = c // 4, c % 4
    m_ = (32 + 2 * e + 2 * i - h - k) % 7
    n = (a + 11 * h + 22 * m_) // 451
    month = (h + m_ - 7 * n + 114) // 31
    day = (h + m_ - 7 * n + 114) % 31 + 1
    return date(year, month, day)


def _nth_weekday(year: int, month: int, weekday: int, n: int) -> date:
    first = date(year, month, 1)
    offset = (weekday - first.weekday()) % 7
    return first + timedelta(days=offset + 7 * (n - 1))


def _last_weekday(year: int, month: int, weekday: int) -> date:
    d = date(year + (month == 12), (month % 12) + 1, 1) - timedelta(days=1)
    return d - timedelta(days=(d.weekday() - weekday) % 7)


def _observed(d: date, *, saturday_to_friday: bool = True) -> date | None:
    """토요일 → 금요일(신정은 예외: NYSE 는 전년 12/31 을 쉬지 않음), 일요일 → 월요일."""
    if d.weekday() == 5:
        return d - timedelta(days=1) if saturday_to_friday else None
    if d.weekday() == 6:
        return d + timedelta(days=1)
    return d


def us_holidays(year: int) -> frozenset[date]:
    """NYSE 정규 휴장일 규칙 계산(국가 애도일 등 임시 휴장은 제외)."""
    fixed = [
        (_observed(date(year, 1, 1), saturday_to_friday=False)),  # 신정
        _nth_weekday(year, 1, 0, 3),  # 마틴 루서 킹
        _nth_weekday(year, 2, 0, 3),  # 대통령의 날
        _easter(year) - timedelta(days=2),  # 성금요일
        _last_weekday(year, 5, 0),  # 현충일
        _observed(date(year, 6, 19)),  # 준틴스
        _observed(date(year, 7, 4)),  # 독립기념일
        _nth_weekday(year, 9, 0, 1),  # 노동절
        _nth_weekday(year, 11, 3, 4),  # 추수감사절
        _observed(date(year, 12, 25)),  # 성탄절
    ]
    return frozenset(d for d in fixed if d is not None and d.year == year)


def us_early_closes(year: int) -> frozenset[date]:
    """NYSE 조기 마감(13:00 ET) 규칙: 추수감사절 다음 날, 7/3·12/24 가 월~목일 때
    (금요일이면 다음 날 토요일 휴일의 대체 휴장이라 전일 휴장, 주말이면 장이 없다)."""
    out = {_nth_weekday(year, 11, 3, 4) + timedelta(days=1)}
    for d in (date(year, 7, 3), date(year, 12, 24)):
        if d.weekday() <= 3:
            out.add(d)
    return frozenset(out)


def is_us_holiday(d: date) -> bool:
    return d in us_holidays(d.year)


def close_hm(market: str, day: date) -> str:
    """그날 정규장 마감 시각(현지 'HH:MM'). US 조기 마감일은 13:00 — 장중 잡·장 상태가 이걸 본다."""
    if market == "US" and day in us_early_closes(day.year):
        return US_EARLY_CLOSE
    return REGULAR[market][1]


def market_close(market: str, day: date) -> time:
    """그날의 정규장 마감 시각(현지). US 조기 마감일은 13:00."""
    if market == "US" and day in us_early_closes(day.year):
        return time(13, 0)
    return MARKET_CLOSE.get(market, time(23, 59))


def has_session(market: str, local_day: date) -> bool:
    """그 시장 현지 날짜에 정규장이 있는가(주말·US 휴장 아님). KR 휴장일은 KIS 조회 없이는 몰라
    평일이면 True."""
    return not _no_session(market, local_day)


def _no_session(market: str, local_day: date) -> bool:
    if local_day.weekday() >= 5:
        return True
    return market == "US" and local_day in us_holidays(local_day.year)


def is_open(market: str, now: datetime) -> bool:
    local = now.astimezone(MARKET_TZ[market])
    if _no_session(market, local.date()):
        return False
    start = REGULAR[market][0]
    return in_window(
        local.time().replace(second=0, microsecond=0), start, close_hm(market, local.date())
    )


def session_over(market: str, now: datetime) -> bool:
    """그 시장 현지 오늘 정규장이 이미 끝났거나(조기 마감 포함) 장이 없는 날(주말·US 휴장). KR
    휴장일은 KIS 조회가 필요해 여기서는 모른다 — 호출자가 `holiday_fn` 을 함께 본다."""
    local = now.astimezone(MARKET_TZ[market])
    if _no_session(market, local.date()):
        return True
    return local.strftime("%H:%M") >= close_hm(market, local.date())


def completed_candles(market: str, candles: list[Any], now: datetime) -> list[Any]:
    """완성된 일봉만: 현지 오늘 봉은 정규장이 끝났을 때만 남기고(장중엔 거래량이 있어도 미완성),
    미래 날짜 봉과 거래량 0 꼬리(개장 전 스텁)는 버린다."""
    local_day = now.astimezone(MARKET_TZ[market]).date()
    last_ok = local_day if session_over(market, now) else local_day - timedelta(days=1)
    return drop_incomplete_tail([c for c in candles if c.date <= last_ok])


def fo_close_hm(last_trade_day: bool) -> str:
    """파생 정규장 마감(KST 'HH:MM'). 최종거래일은 15:20."""
    return FO_LAST_DAY_CLOSE if last_trade_day else FO_SESSION[1]


def is_fo_open(now: datetime, *, last_trade_day: bool = False) -> bool:
    """KRX 파생 정규장 열림(평일 08:45~15:45, 최종거래일 ~15:20). 휴장일 판정은 호출자 몫이다."""
    local = now.astimezone(MARKET_TZ["KR"])
    if local.weekday() >= 5:
        return False
    return in_window(
        local.time().replace(second=0, microsecond=0), FO_SESSION[0], fo_close_hm(last_trade_day)
    )


def index_live(now: datetime) -> bool:
    """현물 지수(코스피200)가 갱신되는 시간(평일 09:00~15:30 KST).
    밖이면 파생 보호선은 지수 조건을 쓰지 않는다."""
    return is_open("KR", now)
