"""파생 레인 공용 타입: 전이표·행 타입·판정 직렬화·실행 컨텍스트."""

from dataclasses import fields
from decimal import Decimal

from tests.features.fo._gate import leg
from trader.features.fo import types
from trader.features.fo.types import FoExecContext, FoVerdict, can_transition


def test_transitions_table():
    assert can_transition("entering", "held")
    assert can_transition("held", "awaiting_settlement")
    assert can_transition("exit_failed", "held")
    assert can_transition("unknown", "closed")
    assert not can_transition("held", "entering")
    assert not can_transition("closed", "held")
    assert not can_transition("settled", "held")
    assert not can_transition("nope", "held")
    assert set(types.TRANSITIONS) == set(types.POSITION_STATUSES)


def test_open_statuses_exclude_terminal():
    assert set(types.POSITION_STATUSES) - set(types.OPEN_POSITION_STATUSES) == {"settled", "closed"}


def test_leg_remaining_and_live():
    assert leg(qty=3, filled_qty=1).remaining == 2
    assert leg(qty=1, filled_qty=5).remaining == 0
    assert leg(status="accepted").live and leg(status="unknown").live
    assert not leg(status="filled").live and not leg(status="cancelled").live


def test_intent_helpers():
    assert types.is_entry("OPEN") and types.is_entry("RESIZE_UP")
    assert types.is_reduce("STOP") and types.is_reduce("CLOSE")
    assert not types.is_reduce("OPEN")


def test_verdict_as_dict_stringifies_decimal():
    v = FoVerdict(
        approved=True, exposure_after=Decimal("1750.5"),
        lifetime_budget={"cap": Decimal("2"), "xs": [Decimal("3")]}, stop_pt=Decimal("345"),
    )  # fmt: skip
    d = v.as_dict()
    assert d["exposure_after"] == "1750.5" and d["stop_pt"] == "345"
    assert d["lifetime_budget"] == {"cap": "2", "xs": ["3"]}


def test_exec_context_has_ports_default_empty(fo_ctx):
    assert "ports" in {f.name for f in fields(FoExecContext)}
    ctx = fo_ctx(ports={})
    assert ctx.ports == {} and ctx.dry_run is False and ctx.historical is False
    assert ctx.now().isoformat().startswith("2026-10-27T01:30")
