"""장중 모니터 순수 규칙(손절·익절·급등락·무효화·정기) — DB·브로커 없이 입력 → Signal."""

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest
from pydantic import ValidationError

from tests.features.spot.helpers import ok_decision
from trader.features.spot import horizon, monitor_rules
from trader.features.spot.schemas import InvalidationRule
from trader.kernel.broker.models import Position

NOW = datetime(2026, 10, 9, 2, 0, tzinfo=UTC)


def _pos(ticker="005930", avg="70000", cur="63000", qty=10):
    return Position(
        ticker=ticker,
        market="KR",
        qty=qty,
        avg_price=Decimal(avg),
        current_price=Decimal(cur),
        eval_amount=Decimal(cur) * qty,
        pnl_pct=float(Decimal(cur) / Decimal(avg) - 1),
    )


def test_check_position_stop_loss_from_decision_and_default():
    pos = _pos(cur="64000")
    dec = ok_decision(stop_loss=65000.0)
    sig = monitor_rules.check_position(pos, dec, Decimal("64000"), stop_loss_pct=-0.15, now=NOW)
    assert [s.kind for s in sig] == ["stop_loss"] and sig[0].price == Decimal("64000")
    # 결정에 손절가가 없으면 평단 -15%
    sig = monitor_rules.check_position(pos, None, Decimal("59000"), stop_loss_pct=-0.15, now=NOW)
    assert [s.kind for s in sig] == ["stop_loss"] and sig[0].detail["level"] == "59500.00"
    # 손절선 위지만 평단 대비 -14%: 급등락 트리거
    kinds = [
        s.kind
        for s in monitor_rules.check_position(
            pos, None, Decimal("60000"), stop_loss_pct=-0.15, now=NOW
        )
    ]
    assert kinds == ["move_7pct"]


def test_invalidation_rules_schema_validates_metric_op_value_days():
    """L16: 무효화 조건은 구조화 필드 — 허용 목록 밖·0 이하·무한대·price 의 days>1 은 거부."""
    InvalidationRule(metric="close", op="<=", value=65000, days=3)
    for bad in (
        {"metric": "rsi", "op": "<", "value": 30},
        {"metric": "price", "op": "==", "value": 1},
        {"metric": "price", "op": "<", "value": 0},
        {"metric": "price", "op": "<", "value": float("inf")},
        {"metric": "price", "op": "<", "value": 10, "days": 3},
        {"metric": "close", "op": "<", "value": 10, "days": 0},
    ):
        with pytest.raises(ValidationError):
            InvalidationRule(**bad)


def test_string_conditions_no_longer_trigger_automatically():
    """L16: 문자열 조건은 설명으로만 남는다 — 옛 파서가 US $100 미만을 버리고 '3일 연속' 을 즉시
    조건으로 읽었다."""
    pos = _pos(avg="70000", cur="65000")
    dec = ok_decision(stop_loss=None, invalidation_conditions=["종가 < 66000"])
    kinds = [
        s.kind
        for s in monitor_rules.check_position(
            pos, dec, Decimal("65500"), stop_loss_pct=-0.15, now=NOW
        )
    ]
    assert kinds == []


def test_close_rule_needs_consecutive_completed_closes_and_price_rule_handles_small_prices():
    pos = _pos(avg="66000", cur="65000")  # 급등락(±7%) 신호가 섞이지 않게 평단을 가깝게
    rule = {"metric": "close", "op": "<=", "value": 65000, "days": 3}
    dec = ok_decision(stop_loss=None, invalidation_rules=[rule])
    kw = {"stop_loss_pct": -0.15, "now": NOW}
    two_of_three = [Decimal("64000"), Decimal("66000"), Decimal("64500")]
    three = [Decimal("64000"), Decimal("64900"), Decimal("64500")]
    q = Decimal("64000")
    assert monitor_rules.check_position(pos, dec, q, closes=two_of_three, **kw) == []
    (sig,) = monitor_rules.check_position(pos, dec, q, closes=three, **kw)
    assert sig.kind == "invalidation" and sig.detail["rule"] == "close <= 65000 (3일)"
    assert monitor_rules.check_position(pos, dec, q, closes=None, **kw) == []  # 종가 없음 = 미판정
    us = _pos(avg="19", cur="18.5")
    dec_us = ok_decision(
        stop_loss=None, invalidation_rules=[{"metric": "price", "op": "<", "value": 18.5}]
    )
    (s2,) = monitor_rules.check_position(us, dec_us, Decimal("18.4"), **kw)  # $100 미만도 본다
    assert s2.kind == "invalidation"


def test_take_profit_move_and_invalidation_signals():
    pos = _pos(avg="70000", cur="80000")
    dec = ok_decision(
        take_profit=80000.0,
        invalidation_rules=[{"metric": "price", "op": "<", "value": 60000}],
    )
    kw = {"stop_loss_pct": -0.15, "now": NOW}
    kinds = [s.kind for s in monitor_rules.check_position(pos, dec, Decimal("80000"), **kw)]
    assert kinds == ["take_profit", "move_7pct"]  # +14% 급등도 함께
    kinds = [s.kind for s in monitor_rules.check_position(pos, dec, Decimal("74000"), **kw)]
    assert kinds == []  # +5.7%: 아무 신호 없음
    dec2 = ok_decision(
        stop_loss=None, invalidation_rules=[{"metric": "price", "op": "<", "value": 66000}]
    )
    kinds = [s.kind for s in monitor_rules.check_position(pos, dec2, Decimal("65500"), **kw)]
    assert kinds == ["invalidation"]  # -6.4% 는 급등락 아님


def test_check_position_move_threshold_follows_profile():
    pos = _pos(avg="70000", cur="73000")
    kw = {"stop_loss_pct": -0.15, "now": NOW}
    # mid(7%) 기본: +4.3% 는 신호 없음, 단기 임계 4% 를 넘기면 move_7pct(임계 표기 포함)
    assert monitor_rules.check_position(pos, None, Decimal("73000"), **kw) == []
    sig = monitor_rules.check_position(
        pos, None, Decimal("73000"), move_pct=Decimal("0.04"), **kw
    )
    assert [s.kind for s in sig] == ["move_7pct"] and sig[0].detail["threshold"] == "4.00%"
    # 장기 임계 10%: +9% 도 조용
    assert (
        monitor_rules.check_position(pos, None, Decimal("76300"), move_pct=Decimal("0.10"), **kw)
        == []
    )


def test_periodic_signal_rules(settings):
    prof = settings.risk.horizon_profiles["short"]
    kw = {"profile": prof, "last_periodic_at": None, "count_today": 0, "now": NOW}
    # 변동 2%(= move_pct/2) 이상이어야 한다
    assert monitor_rules.periodic_signal(Decimal("70700"), Decimal("70000"), **kw) is None
    sig = monitor_rules.periodic_signal(Decimal("71500"), Decimal("70000"), **kw)
    assert sig is not None and sig.kind == "periodic" and sig.detail["ref_price"] == "70000"
    # 간격(60분) 안이면 없음, 지나면 다시
    kw2 = kw | {"last_periodic_at": NOW - timedelta(minutes=30)}
    assert monitor_rules.periodic_signal(Decimal("71500"), Decimal("70000"), **kw2) is None
    kw3 = kw | {"last_periodic_at": NOW - timedelta(minutes=61)}
    assert monitor_rules.periodic_signal(Decimal("71500"), Decimal("70000"), **kw3) is not None
    # 운영일 상한, mid/long 프로필(periodic_check_min=0)은 없음
    capped = kw | {"count_today": horizon.PERIODIC_DAILY_CAP}
    assert monitor_rules.periodic_signal(Decimal("71500"), Decimal("70000"), **capped) is None
    mid = settings.risk.horizon_profiles["mid"]
    assert (
        monitor_rules.periodic_signal(Decimal("80000"), Decimal("70000"), **(kw | {"profile": mid}))
        is None
    )


def test_close_rule_keeps_session_alignment_and_skips_on_missing_session():
    """PR #63 Codex: 빠진 거래일을 지우면 연속이 아닌 종가가 연속처럼 보인다 — 거래일 정렬을 지키고
    필요한 종가 중 하나라도 없으면(빠진 날·오래된 마지막 값) 미판정. (종가표 읽기는 호출자 몫 —
    여기서는 날짜별 값 → 정렬된 종가 목록 변환부터 본다.)"""
    days = [date(2026, 10, 20), date(2026, 10, 21), date(2026, 10, 22), date(2026, 10, 23)]
    column = dict(zip(days, [90.0, float("nan"), 90.0, 90.0], strict=True))
    rule = {"metric": "close", "op": "<", "value": 100, "days": 3}
    dec = {"invalidation_rules": [rule]}
    assert monitor_rules.needs_closes(dec) and not monitor_rules.needs_closes({})
    closes = monitor_rules.closes_from_column(column)
    assert closes[-3] is None and len(closes) == 4  # 빠진 날 자리가 남아 있다
    assert monitor_rules.rule_hit(rule, Decimal("90"), closes) is None
    two = {"metric": "close", "op": "<", "value": 100, "days": 2}
    assert monitor_rules.rule_hit(two, Decimal("90"), closes) is True


def test_monotone_stop_keeps_highest_floor():
    """손절선은 올리기만 한다(L5): 바닥이 결정 손절가보다 높으면 바닥으로, 아니면 원본 그대로."""
    assert monitor_rules.monotone_stop({"stop_loss": 90}, Decimal("94")) == {"stop_loss": 94.0}
    assert monitor_rules.monotone_stop(None, Decimal("94")) == {"stop_loss": 94.0}
    assert monitor_rules.monotone_stop({"stop_loss": 96}, Decimal("94")) == {"stop_loss": 96}
    assert monitor_rules.monotone_stop(None, None) is None
