"""fo health 영역: 포지션 막힘(critical)·정산 지연(warning)·옵션 매도 만기 D-1(critical)·
파생 마감 뒤 주문(watch, 파생 시계)."""

from datetime import UTC, datetime
from types import SimpleNamespace

from trader.features.fo import health
from trader.kernel.ops import areas
from trader.kernel.ops.types import OpsContext

NOW = datetime(2026, 10, 27, 1, 30, tzinfo=UTC)  # 화 10:30 KST


class Accounts:
    def __init__(self, *accts):
        self._a = list(accts)

    def accounts(self):
        return [a for a in self._a if a.asset == "stock"]

    def fo_accounts(self):
        return [a for a in self._a if a.asset == "fo"]


def acct(id_, asset="stock"):
    return SimpleNamespace(id=id_, env="paper", asset=asset, markets=["KR"])


SPOT, FO = acct("paper-main"), acct("paper-fo", asset="fo")


def ctx(conn, project, *accts, now=NOW):
    return OpsContext(
        conn=conn,
        settings=None,
        state=project / "state",
        now=now,
        ports={"accounts": Accounts(*accts)},
        jobs=[],
        features=[],
    )


def pos(conn, status, vehicle="FUTURES", last_trade_date=None, account_id="paper-fo"):
    cur = conn.execute(
        "INSERT INTO fo_positions(account_id, code, kind, contract_side, direction, vehicle, "
        "status, last_trade_date, created_at) VALUES (?,?,?,?,?,?,?,?,?)",
        (account_id, "101W12", "future", "long", "long", vehicle, status, last_trade_date,
         NOW.isoformat()),
    )  # fmt: skip
    conn.commit()
    return cur.lastrowid


def order(conn, created_at, status="accepted", asset="fo", account_id="paper-fo"):
    conn.execute(
        "INSERT INTO orders(account_id, ticker, market, side, qty, order_type, order_no, status, "
        "message, mode, created_at, asset) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        (account_id, "101W12", "KR", "BUY", 1, "LIMIT", "F1", status, "", "auto", created_at,
         asset),
    )  # fmt: skip
    conn.commit()


def test_area_key_is_feature_id_and_registered():
    from trader.features.fo.feature import FEATURE

    assert health.AREA.key == "fo" and FEATURE.health_areas == (health.AREA,)


def test_health_fo_area_ok_without_fo_accounts(conn, project):
    pos(conn, "unknown")
    assert health.check(ctx(conn, project, SPOT)) == []
    assert health.check(ctx(conn, project, SPOT, FO)) != []


def test_health_fo_area_levels(conn, project):
    assert health.check(ctx(conn, project, SPOT, FO)) == []
    a = pos(conn, "unknown")
    b = pos(conn, "exit_failed")
    pos(conn, "awaiting_settlement", last_trade_date="2026-10-25")  # 2일 지남 → 지연
    pos(conn, "awaiting_settlement", last_trade_date="2026-10-26")  # 1일 — 유예 안
    pos(conn, "held", vehicle="OPTION_SELL", last_trade_date="2026-10-28")  # D-1
    pos(conn, "held", vehicle="OPTION_SELL", last_trade_date="2026-11-12")  # 멀다
    pos(conn, "closed", vehicle="OPTION_SELL", last_trade_date="2026-10-28")  # 청산됨
    pos(conn, "unknown", account_id="other-fo")  # 등록되지 않은 계좌
    by = {i.key: i for i in health.check(ctx(conn, project, SPOT, FO))}
    assert {k: (i.level, i.count) for k, i in by.items()} == {
        "fo.position_blocked": ("critical", 2),
        "fo.settlement_overdue": ("warning", 1),
        "fo.option_sell_expiry": ("critical", 1),
    }
    assert by["fo.position_blocked"].extra == {"ids": [a, b]}
    assert all(i.link == "/system#fo" for i in by.values())


def test_health_fo_stale_orders_use_fo_clock(conn, project):
    order(conn, "2026-10-27T00:00:00+00:00")  # 오늘 09:00 KST
    order(conn, "2026-10-27T00:10:00+00:00", status="unknown")
    order(conn, "2026-10-27T00:20:00+00:00", status="filled")
    # 15:40 KST: 현물 마감(15:30) 뒤지만 파생 마감(15:45) 전 — 둘 다 조용
    before = datetime(2026, 10, 27, 6, 40, tzinfo=UTC)
    assert health.check(ctx(conn, project, SPOT, FO, now=before)) == []
    after = datetime(2026, 10, 27, 6, 50, tzinfo=UTC)  # 15:50 KST
    c = ctx(conn, project, SPOT, FO, now=after)
    items = health.check(c)
    assert [(i.key, i.level, i.count, i.link) for i in items] == [
        ("fo.orders_stale", "watch", 2, "/")
    ]
    # 커널 현물 orders 영역은 파생 주문을 세지 않는다(unknown·마감 뒤 미정리 모두)
    keys = {i.key for i in areas.check_orders(c)}
    assert not keys & {"orders.unknown", "orders.stale"}
