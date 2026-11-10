"""spot health 영역: 체결 매칭 모호·야간 대조 불일치(warning)·미실행(watch)·예정 시각·건너뜀."""

import json
from datetime import UTC, date, datetime
from types import SimpleNamespace

from trader.features.spot import health
from trader.features.spot.jobs import RECONCILE
from trader.kernel.config import KST
from trader.kernel.daemon.heartbeat import FILE_NAME
from trader.kernel.daemon.jobs import ScheduledJob
from trader.kernel.ledger.control import set_flag
from trader.kernel.ops.types import OpsContext

NOW = datetime(2026, 10, 23, 8, 30, tzinfo=UTC)  # 금 17:30 KST — 대조 예정 16:40 + 유예 30분 뒤


class Accounts:
    def __init__(self, *accts, holiday=None):
        self._a = list(accts)
        if holiday is not None:
            self.holiday_fn = lambda: holiday

    def accounts(self):
        return list(self._a)


def acct(id_, markets=("KR",)):
    return SimpleNamespace(id=id_, env="paper", asset="stock", markets=list(markets))


def ctx(conn, project, *accts, now=NOW, jobs=(), holiday=None):
    return OpsContext(
        conn=conn,
        settings=None,
        state=project / "state",
        now=now,
        ports={"accounts": Accounts(*accts, holiday=holiday)},
        jobs=list(jobs),
        features=[],
    )


def test_area_key_is_feature_id_and_registered():
    from trader.features.spot.feature import FEATURE

    assert health.AREA.key == "spot" and FEATURE.health_areas == (health.AREA,)


def test_fill_ambiguous_and_reconcile_mismatch_warning(conn, project):
    amb = {"cases": [{"account_id": "paper-main", "ticker": "005930"}], "candidates": 2}
    set_flag(conn, "fill_ambiguous:KR:2026-10-23", amb)
    set_flag(
        conn,
        "reconcile:paper-main:KR:2026-10-23",
        {"ok": False, "mismatches": [{"ticker": "005930"}], "open_orders": [{"id": 1}]},
    )
    by = {i.key: i for i in health.check(ctx(conn, project, acct("paper-main")))}
    assert set(by) == {"spot.fill_ambiguous.KR", "spot.reconcile.paper-main.KR"}
    fa = by["spot.fill_ambiguous.KR"]
    assert fa.level == "warning" and fa.link == "/#orders-timeline" and fa.extra == amb
    rc = by["spot.reconcile.paper-main.KR"]
    assert rc.level == "warning" and rc.count == 2 and rc.link == "/accounts/paper-main"


def test_reconcile_watch_after_due_plus_grace(conn, project):
    early = datetime(2026, 10, 23, 8, 0, tzinfo=UTC)  # 17:00 KST — 16:40 + 30분 전
    assert health.check(ctx(conn, project, acct("paper-main"), now=early)) == []
    items = health.check(ctx(conn, project, acct("paper-main")))
    assert [(i.key, i.level, i.link) for i in items] == [
        ("spot.reconcile.paper-main.KR", "watch", "/system#daemon")
    ]
    weekend = datetime(2026, 10, 24, 8, 30, tzinfo=UTC)  # 토 17:30 KST
    assert health.check(ctx(conn, project, acct("paper-main"), now=weekend)) == []
    set_flag(conn, "reconcile:paper-main:KR:2026-10-23", {"ok": True})
    assert health.check(ctx(conn, project, acct("paper-main"))) == []


def test_reconcile_due_follows_cron_timezone():
    """US 대조 cron 이 KST 06:40 이면 거래소 시각으로는 그날(ET) 17:40 — 장중에 '미실행' 으로
    잡지 않는다."""
    kw = {"hour": 6, "minute": 40, "day_of_week": "tue-sat", "timezone": KST}
    job = ScheduledJob("us_reconcile", RECONCILE, "US", kw)
    now = datetime(2026, 10, 27, 15, tzinfo=UTC)
    due = health.reconcile_due("US", date(2026, 10, 27), [job], now)
    assert (due.month, due.day, due.hour, due.minute) == (10, 27, 17, 40)
    fallback = health.reconcile_due("KR", date(2026, 10, 27), [job], NOW)
    assert (fallback.hour, fallback.minute, fallback.tzinfo is not None) == (16, 40, True)


def test_reconcile_skipped_today_suppresses_watch(conn, project):
    state = project / "state"
    day = date(2026, 10, 23)
    assert not health.reconcile_skipped_today(state, "KR", day, None)
    hb = {"jobs": {"kr_reconcile": {"last_status": "skipped", "last_end": NOW.isoformat()}}}
    (state / FILE_NAME).write_text(json.dumps(hb), encoding="utf-8")
    assert health.reconcile_skipped_today(state, "KR", day, None)
    assert not health.reconcile_skipped_today(state, "KR", date(2026, 10, 22), None)
    assert health.check(ctx(conn, project, acct("paper-main"))) == []
    # US 휴장(추수감사절)은 NYSE 규칙, KR 휴장은 accounts 포트의 holiday_fn
    assert health.reconcile_skipped_today(state, "US", date(2026, 11, 26), None)
    (state / FILE_NAME).unlink()
    assert health.reconcile_skipped_today(state, "KR", day, lambda d: d == day)
    holiday = lambda m, d: m == "KR" and d == day  # noqa: E731
    assert health.check(ctx(conn, project, acct("paper-main"), holiday=holiday)) == []


def test_holiday_fn_returning_none_falls_back(conn, project):
    """커널 AccountsPort.holiday_fn() 은 None 을 돌려줄 수 있다 — 영역이 예외로 끝나지 않고
    폴백한다."""
    set_flag(conn, "fill_ambiguous:KR:2026-10-23", {"candidates": 2})
    port = Accounts(acct("paper-main"))
    port.holiday_fn = lambda: None
    c = OpsContext(conn=conn, settings=None, state=project / "state", now=NOW,
                   ports={"accounts": port}, jobs=[], features=[])  # fmt: skip
    keys = {(i.key, i.level) for i in health.check(c)}
    assert keys == {
        ("spot.fill_ambiguous.KR", "warning"),
        ("spot.reconcile.paper-main.KR", "watch"),
    }
