"""권리 일정 블랙아웃(Stock D-113): 수집·창 판정·기본 끔."""

from datetime import date

from trader.features.regime import blackout
from trader.features.regime.settings import BlackoutCfg

D = date(2026, 10, 27)


class Market:
    def __init__(self, fail=()):
        self.fail = set(fail)

    def ksd_schedule(self, kind, start, end):
        if kind in self.fail:
            raise RuntimeError("x")
        return [{"ticker": "005930", "kind": kind, "date": "2026-10-30", "name": "삼성전자"}]


def test_collect_and_reason_window(conn):
    on = BlackoutCfg(enabled=True, kinds=["paidin", "rev_split"])
    r = blackout.collect(Market(fail=["rev_split"]), on, today=D)
    assert len(r["events"]) == 1 and "rev_split" in r["errors"]
    blackout.store(conn, r)
    assert blackout.reason(conn, "005930", D, on) == "권리 일정: 유상증자 기준일 2026-10-30"
    assert blackout.reason(conn, "005930", date(2026, 10, 24), on) is None  # 6일 전
    assert blackout.reason(conn, "005930", date(2026, 11, 1), on) is not None  # 2일 뒤
    assert blackout.reason(conn, "005930", date(2026, 11, 2), on) is None
    assert blackout.reason(conn, "000660", D, on) is None
    assert blackout.reason(conn, "005930", D, BlackoutCfg()) is None  # 기본 끔


def test_store_keeps_last_success_per_kind_and_reports_stale(conn):
    """2-O9: 종류별 마지막 성공분을 합친다 — 실패한 종류는 지난 일정·수집 시각 유지, 성공한 종류만
    교체. `stale_kinds` 는 수집 시각이 STALE_DAYS 넘은 종류(없으면 None 일수)."""
    from datetime import UTC, datetime, timedelta

    from trader.kernel.ledger import control

    on = BlackoutCfg(enabled=True, kinds=["paidin", "rev_split"])
    t1 = datetime(2026, 10, 27, 22, 5, tzinfo=UTC)
    r1 = blackout.collect(Market(), on, today=D, now=t1)
    blackout.store(conn, r1)
    r2 = blackout.collect(Market(fail=["rev_split"]), on, today=D, now=t1 + timedelta(days=1))
    blackout.store(conn, r2)
    data = control.get_flag(conn, blackout.FLAG)
    assert sorted(e["kind"] for e in data["events"]) == ["paidin", "rev_split"]
    assert data["fetched_at"] == {"paidin": r2["at"], "rev_split": r1["at"]}
    assert blackout.reason(conn, "005930", D, on) is not None
    assert blackout.stale_kinds(data, on, now=t1 + timedelta(days=3)) == {}
    assert blackout.stale_kinds(data, on, now=t1 + timedelta(days=4)) == {"rev_split": 4}
    # 설정에서 빠진 종류의 지난 일정은 버리고, 수집 기록이 없는 종류는 None
    only = BlackoutCfg(enabled=True, kinds=["paidin"])
    blackout.store(conn, blackout.collect(Market(), only, today=D, now=t1))
    data = control.get_flag(conn, blackout.FLAG)
    assert [e["kind"] for e in data["events"]] == ["paidin"]
    assert blackout.stale_kinds(data, on, now=t1) == {"rev_split": None}
