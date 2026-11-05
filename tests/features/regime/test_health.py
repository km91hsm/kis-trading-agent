"""regime health 영역: 킬스위치·입출금 의심·최근 5거래일 실현손익 단계."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from trader.features.regime import health, killswitch
from trader.kernel.ledger.control import set_flag
from trader.kernel.ops.types import OpsContext

NOW = datetime(2026, 10, 27, 3, 0, tzinfo=UTC)  # 화 12:00 KST


class Accounts:
    def __init__(self, *accts):
        self._a = list(accts)

    def accounts(self):
        return list(self._a)


def acct(id_, markets=("KR",), asset="stock"):
    return SimpleNamespace(id=id_, env="paper", asset=asset, markets=list(markets))


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


def nav(conn, account_id, value, market="KR", d="2026-10-26"):
    conn.execute(
        "INSERT INTO account_nav(account_id, date, market, nav, cash, created_at) "
        "VALUES (?,?,?,?,?,?)",
        (account_id, d, market, str(value), "0", NOW.isoformat()),
    )
    conn.commit()


def test_area_key_is_feature_id_and_registered():
    from trader.features.regime.feature import FEATURE

    assert health.AREA.key == "regime" and FEATURE.health_areas == (health.AREA,)


def test_loss_area_levels_and_killswitch(conn, project, monkeypatch):
    assert health.check(ctx(conn, project, acct("paper-main"))) == []
    killswitch.engage(conn, reason="수동 정지", source="cli")
    accts = [acct(f"paper-{i}") for i in range(5)] + [acct("paper-fo", asset="fo")]
    for i, value in enumerate((1_000_000, 1_000_000, 1_000_000, 1_000_000, 0)):
        nav(conn, f"paper-{i}", value)
    nav(conn, "paper-fo", 1_000_000)
    pnl = {"paper-0": -10_000, "paper-1": -30_000, "paper-2": -60_000, "paper-3": -120_000}
    pnl |= {"paper-4": -1, "paper-fo": -500_000}
    monkeypatch.setattr(health, "realized_pnl_5d", lambda c, a, d, market=None: pnl[a])
    by = {i.key: i for i in health.check(ctx(conn, project, *accts))}
    ks = by.pop("regime.killswitch")
    assert ks.level == "critical" and ks.link == "/system" and "수동 정지" in ks.text
    assert {k: i.level for k, i in by.items()} == {
        "regime.loss.paper-1.KR": "watch",
        "regime.loss.paper-2.KR": "warning",
        "regime.loss.paper-3.KR": "critical",
    }  # -1% 는 단계 밖, NAV 0 은 생략, 파생 계좌는 제외
    hit = by["regime.loss.paper-2.KR"]
    assert hit.extra == {"pct": -0.06} and hit.link == "/accounts/paper-2"
    assert "-6.00%" in hit.text


def test_loss_area_shows_flow_suspicion(conn, project):
    """2-O16: 입출금 의심은 warning — 기준 재설정 명령과 전역 효과를 안내, 운영일 2일 뒤 사라짐."""
    set_flag(
        conn,
        "killswitch_flow:paper-main:KR",
        {"date": "2026-10-27", "residual": "-2500", "prev_nav": "10500", "nav": "8000"},
    )
    items = health.check(ctx(conn, project, acct("paper-main")))
    flow = [i for i in items if i.key == "regime.flow.paper-main.KR"]
    assert flow and flow[0].level == "warning" and flow[0].link == "/accounts/paper-main"
    assert "killswitch off" in flow[0].text and "모든 계좌" in flow[0].text
    later = ctx(conn, project, acct("paper-main"), now=NOW + timedelta(days=3))
    assert not [i for i in health.check(later) if i.key.startswith("regime.flow")]


def test_bad_flow_date_skips_only_that_item(conn, project):
    """flow 기록의 날짜가 깨져도 그 항목만 건너뛴다 — 영역 전체가 `.error` 가 되지 않는다(T3)."""
    bad = {"date": "not-a-date", "residual": "-1", "prev_nav": "1", "nav": "0"}
    set_flag(conn, "killswitch_flow:paper-1:KR", bad)
    good = {"date": "2026-10-27", "residual": "-2500", "prev_nav": "10500", "nav": "8000"}
    set_flag(conn, "killswitch_flow:paper-2:KR", good)
    items = health.check(ctx(conn, project, acct("paper-1"), acct("paper-2")))
    keys = {i.key for i in items}
    assert "regime.flow.paper-2.KR" in keys and "regime.flow.paper-1.KR" not in keys
