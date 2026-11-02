"""spot 트리거 원장(옛 test_repo 이식)과 손절 바닥 `max_stop_since`."""

from datetime import date, datetime
from decimal import Decimal

from tests.features.spot.conftest import D
from tests.features.spot.helpers import ok_decision
from trader.features.spot import ledger
from trader.kernel.execution import ledger as exec_ledger


def _ingest(conn, d, stage, **over):
    ledger.ingest_checkpoint(conn, d, "KR", "005930", stage, {"data": ok_decision(**over)})


def test_v4_triggers_and_fill_dedupe(conn):
    tables = {r["name"] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert "triggers" in tables
    _ingest(conn, D, "30_decision")
    dec_id = ledger.decision_id(conn, D, "KR", "005930")
    oid = exec_ledger.insert_order(
        conn,
        decision_id=dec_id,
        account_id="paper-main",
        ticker="005930",
        market="KR",
        side="BUY",
        qty=2,
        order_type="LIMIT",
        price=Decimal("100"),
        status="accepted",
        order_no="1",
        message="",
        mode="auto",
    )
    kw = dict(
        order_id=oid,
        account_id="paper-main",
        market="KR",
        ticker="005930",
        side="BUY",
        qty=2,
        price=Decimal("100"),
        filled_at="2026-10-09T01:00:00+00:00",
        order_no="1",
    )
    assert isinstance(exec_ledger.insert_fill(conn, **kw), int)
    assert exec_ledger.insert_fill(conn, **kw) is None  # 같은 체결은 무시
    assert conn.execute("SELECT COUNT(*) FROM fills").fetchone()[0] == 1
    tid = ledger.insert_trigger(
        conn,
        market="KR",
        ticker="005930",
        kind="move_7pct",
        detail={"pct": 0.08},
        price=Decimal("77000"),
        decision_id=dec_id,
        account_id=None,
        created_at="2026-10-09T01:00:00+00:00",
    )
    assert ledger.last_trigger_handled_at(conn, "KR", "005930") is None
    ledger.mark_trigger_handled(conn, tid, "sess-1", handled_at="2026-10-09T02:00:00+00:00")
    assert isinstance(
        datetime.fromisoformat(ledger.last_trigger_handled_at(conn, "KR", "005930")), datetime
    )
    row = conn.execute("SELECT kind, detail, session_id FROM triggers").fetchone()
    assert row["kind"] == "move_7pct" and '"pct"' in row["detail"] and row["session_id"] == "sess-1"
    t = ledger.get_trigger(conn, tid)
    assert t["detail"] == {"pct": 0.08} and t["price"] == "77000"


def test_unhandled_trigger_id_today_only(conn):
    def mk(created_at, price=None):
        return ledger.insert_trigger(
            conn, market="KR", ticker="005930", kind="move_7pct", detail={}, price=price,
            decision_id=None, account_id=None, created_at=created_at,
        )  # fmt: skip

    old = mk("2026-10-08T01:00:00+00:00")
    assert ledger.unhandled_trigger_id(conn, "KR", "005930", "move_7pct", D) is None
    today = mk("2026-10-09T01:00:00+00:00", Decimal("77000"))
    assert old != today
    assert ledger.unhandled_trigger_id(conn, "KR", "005930", "move_7pct", D) == today
    assert ledger.unhandled_trigger_id(conn, "KR", "005930", "other", D) is None
    assert ledger.latest_trigger_price(conn, "KR", "005930", D) == Decimal("77000")
    assert ledger.latest_trigger_price(conn, "KR", "005930", date(2026, 10, 10)) is None
    ledger.mark_trigger_handled(conn, today, "s", handled_at="2026-10-09T02:00:00+00:00")
    assert ledger.unhandled_trigger_id(conn, "KR", "005930", "move_7pct", D) is None
    assert ledger.reevaluated_between(
        conn, "KR", "005930", "move_7pct", "2026-10-09T00:00:00+00:00", "2026-10-10T00:00:00+00:00"
    )
    ledger.mark_trigger_handled(conn, old, "실패:기동", handled_at="2026-10-09T03:00:00+00:00")
    assert not ledger.reevaluated_between(
        conn, "KR", "005930", "move_7pct", "2026-10-09T02:30:00+00:00", "2026-10-10T00:00:00+00:00"
    )
    assert ledger.get_trigger(conn, 999) is None


def test_max_stop_since_keeps_floor_across_same_day_overwrite_and_ignores_sell_trim(conn):
    """같은 날 재평가가 행을 덮어써도 손절선 바닥은 최고값으로 남고(2-O1),
    SELL/TRIM 결정의 손절가는 바닥 계산에 들어가지 않는다(2-O2)."""
    _ingest(conn, D, "30_decision", stop_loss=65000.0)
    did = ledger.decision_id(conn, D, "KR", "005930")
    _ingest(conn, D, "31_decision_r1", action="HOLD", target_weight=0.0, stop_loss=None)
    assert ledger.max_stop_since(conn, "KR", "005930", did) == Decimal("65000")
    # 더 낮은 HOLD 손절가도 바닥을 내리지 못한다
    _ingest(conn, D, "31_decision_r2", action="HOLD", target_weight=0.0, stop_loss=60000.0)
    assert ledger.max_stop_since(conn, "KR", "005930", did) == Decimal("65000")
    # 게이트가 거부한 TRIM 재평가가 현재가 바로 아래 손절가를 적어도 바닥이 되지 않는다
    _ingest(conn, D, "31_decision_r3", action="TRIM", target_weight=0.05, stop_loss=69900.0)
    assert ledger.max_stop_since(conn, "KR", "005930", did) == Decimal("65000")
    _ingest(conn, date(2026, 10, 10), "30_decision", action="SELL", stop_loss=69950.0)
    assert ledger.max_stop_since(conn, "KR", "005930", did) == Decimal("65000")
    assert ledger.max_stop_since(conn, "KR", "005930", None) is None
