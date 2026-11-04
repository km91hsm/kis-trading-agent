"""M10 A3: `LegStore` — 커널 `IdemStore` 를 fo 다리 행으로 구현한다."""

from __future__ import annotations

from datetime import timedelta

import pytest

from tests.features.fo._support import ACCT, NOW, _bal, _open, _recover
from trader.features.fo import store
from trader.features.fo.legstore import LegStore, leg_id_of
from trader.kernel.execution.idem import IdemStore

ISO = "2026-10-27T01:30:00+00:00"


def test_legstore_claim_is_cas_prepared_to_sending(conn):
    _pos, leg = _open(conn)
    ls = LegStore(conn)
    key = f"leg:{leg.id}"
    assert ls.claim(key, now=ISO) is True
    assert ls.claim(key, now=ISO) is False
    got = store.get_leg(conn, leg.id)
    assert (got.status, got.sent_at) == ("sending", ISO)


def test_legstore_bind_and_release(conn):
    _pos, leg = _open(conn)
    ls = LegStore(conn)
    ls.bind(f"leg:{leg.id}", order_id=7, at=ISO)
    got = store.get_leg(conn, leg.id)
    assert (got.order_id, got.accepted_at) == (7, ISO)
    # release 는 prepared 만 취소한다
    ls.release(f"leg:{leg.id}")
    assert store.get_leg(conn, leg.id).status == "cancelled"
    _pos2, leg2 = _open(conn, code="105W12", max_positions=2)
    ls.claim(f"leg:{leg2.id}", now=ISO)
    ls.release(f"leg:{leg2.id}")
    assert store.get_leg(conn, leg2.id).status == "sending"


def test_legstore_mark_sent_and_protocol_stubs(conn):
    _pos, leg = _open(conn)
    ls = LegStore(conn)
    ls.mark_sent(f"leg:{leg.id}", sent_at=ISO)
    assert store.get_leg(conn, leg.id).sent_at == ISO
    assert ls.has_approval(f"leg:{leg.id}") is False
    ok, seen = ls.claim_reserved(
        f"leg:{leg.id}", now=ISO, account_id=ACCT, market="KR", ticker="105W11", side="BUY",
        marks={}, recheck=lambda inflight: (True, None),
    )  # fmt: skip
    assert (ok, seen) == (True, [])
    assert store.get_leg(conn, leg.id).status == "sending"


def test_legstore_claim_reserved_recheck_reject_reverts(conn):
    _pos, leg = _open(conn)
    ls = LegStore(conn)
    ok, _ = ls.claim_reserved(
        f"leg:{leg.id}", now=ISO, account_id=ACCT, market="KR", ticker="105W11", side="BUY",
        marks={}, recheck=lambda inflight: (False, None),
    )  # fmt: skip
    assert ok is False
    got = store.get_leg(conn, leg.id)
    assert (got.status, got.sent_at) == ("prepared", None)


def test_legstore_inflight_lists_live_legs(conn):
    _pos, leg = _open(conn)
    ls = LegStore(conn)
    rows = ls.inflight(account_id=ACCT, market="KR", marks={})
    assert [r["key"] for r in rows] == [f"leg:{leg.id}"]
    assert ls.inflight(account_id="other", market="KR", marks={}) == []
    assert [r["key"] for r in ls.list_all()] == [f"leg:{leg.id}"]
    ls.release(f"leg:{leg.id}")
    assert ls.inflight(account_id=ACCT, market="KR", marks={}) == []


def test_legstore_satisfies_protocol(conn):
    names = {n for n in vars(IdemStore) if not n.startswith("_")}
    assert len(names) == 8
    ls = LegStore(conn)
    assert all(callable(getattr(ls, n, None)) for n in names)


def test_leg_id_of_parses_key():
    assert leg_id_of("leg:12") == 12
    for bad in ("12", "leg:", "leg:x", "buy:1", "leg:-1"):
        with pytest.raises(ValueError):
            leg_id_of(bad)


def test_recover_uses_injected_clock(conn):
    """A18: 전송 안 된 prepared 다리의 낡음은 벽시계가 아니라 주입한 now 로 판정한다."""
    _p1, old = _open(conn)
    _p2, fresh = _open(conn, code="105W12", max_positions=2)
    conn.execute(
        "UPDATE fo_order_legs SET created_at = ? WHERE id = ?",
        ((NOW + timedelta(minutes=5) - timedelta(seconds=200)).isoformat(), old.id),
    )
    conn.execute(
        "UPDATE fo_order_legs SET created_at = ? WHERE id = ?",
        ((NOW + timedelta(minutes=5) - timedelta(seconds=30)).isoformat(), fresh.id),
    )
    conn.commit()
    rep = _recover(conn, _bal())  # now = NOW + 5분
    assert store.get_leg(conn, old.id).status == "cancelled"
    assert store.get_leg(conn, fresh.id).status == "prepared"
    assert rep.legs_fixed == 1


def test_legstore_joins_enclosing_store_tx(conn):
    """`store.tx` 안에서 부르면 합류만 한다(commit 은 바깥 몫) — 실행기 `_transmit` 이 orders
    행 INSERT 와 `bind` 를 한 `store.tx` 로 묶는다(A1). 단독으로 부르면 스스로 commit 한다."""
    _pos, leg = _open(conn)
    ls = LegStore(conn)
    with pytest.raises(RuntimeError), store.tx(conn):
        ls.bind(f"leg:{leg.id}", order_id=7, at=ISO)
        assert conn.in_transaction
        raise RuntimeError("바깥 실패")
    assert store.get_leg(conn, leg.id).order_id is None
    ls.bind(f"leg:{leg.id}", order_id=7, at=ISO)
    assert not conn.in_transaction and store.get_leg(conn, leg.id).order_id == 7
