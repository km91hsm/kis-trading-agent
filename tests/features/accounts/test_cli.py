from decimal import Decimal

import yaml
from typer.testing import CliRunner

from trader.features.accounts import modes
from trader.features.accounts.settings import load_accounts
from trader.kernel.cli.main import app
from trader.kernel.config.secrets import Secrets
from trader.kernel.ledger import db


def _accounts(project, extra=()):
    data = {
        "accounts": [
            {"id": "paper-main", "env": "paper", "cano": "0", "markets": ["KR"], "mode": "auto"},
            {"id": "real-main", "env": "real", "cano": "1", "markets": ["KR"], "mode": "off"},
            *extra,
        ]
    }
    (project / "config" / "accounts.yaml").write_text(yaml.safe_dump(data), encoding="utf-8")


def _by_id(id_):
    return next(a for a in load_accounts(secrets=Secrets()) if a.id == id_)


def test_accounts_mode_sets_and_shows(project):
    _accounts(project)
    runner = CliRunner()
    r = runner.invoke(app, ["accounts", "mode", "paper-main", "approve"])
    assert r.exit_code == 0, r.stdout
    conn = db.connect()
    assert modes.get_account_mode(conn, _by_id("paper-main")) == "approve"
    conn.close()
    r = runner.invoke(app, ["accounts", "show"])
    assert (
        r.exit_code == 0
        and "paper-main" in r.stdout
        and "approve" in r.stdout
        and "일시정지" in r.stdout
    )


def test_accounts_mode_real_auto_requires_confirm(project):
    _accounts(project)
    runner = CliRunner()
    r = runner.invoke(app, ["accounts", "mode", "real-main", "auto"])
    assert r.exit_code == 1 and "LIVE" in (r.stdout + (r.stderr or ""))
    conn = db.connect()
    assert conn.execute("SELECT COUNT(*) FROM account_modes").fetchone()[0] == 0
    conn.close()
    r = runner.invoke(app, ["accounts", "mode", "real-main", "auto", "--confirm", "LIVE"])
    assert r.exit_code == 0
    conn = db.connect()
    assert modes.live_confirmed(conn, "real-main")
    conn.close()


def test_accounts_mode_rejects_unknown(project):
    _accounts(project)
    runner = CliRunner()
    assert runner.invoke(app, ["accounts", "mode", "nope", "auto"]).exit_code == 1
    assert runner.invoke(app, ["accounts", "mode", "paper-main", "fast"]).exit_code == 1


def test_accounts_pause_resume(project):
    _accounts(project)
    runner = CliRunner()
    assert runner.invoke(app, ["accounts", "pause"]).exit_code == 0
    conn = db.connect()
    assert modes.is_paused(conn)
    conn.close()
    assert runner.invoke(app, ["accounts", "resume"]).exit_code == 0
    conn = db.connect()
    assert not modes.is_paused(conn)
    conn.close()


def test_accounts_show_reports_effective_mode(project):
    _accounts(project)
    conn = db.connect()
    from trader.kernel.registry import discover
    from trader.kernel.registry.contracts import all_migrations

    db.init_schema(conn, all_migrations(discover()))
    conn.execute("INSERT INTO account_modes VALUES ('real-main','auto',NULL,'t')")
    conn.commit()
    conn.close()
    r = CliRunner().invoke(app, ["accounts", "show"])
    assert r.exit_code == 0, r.stdout
    line = next(x for x in r.stdout.splitlines() if x.startswith("real-main"))
    assert "off" in line and "live_confirmed" in line


def test_accounts_pause_reports_config_error_as_one_line(project):
    (project / "config" / "accounts.yaml").write_text("accounts: [{id: x}]", encoding="utf-8")
    r = CliRunner().invoke(app, ["accounts", "pause"])
    assert (
        r.exit_code == 1
        and "오류:" in (r.stdout + (r.stderr or ""))
        and "Traceback" not in (r.stdout + (r.stderr or ""))
    )


def test_accounts_show_real_fo_account_is_off(project):
    _accounts(
        project,
        extra=[
            {
                "id": "real-fo",
                "env": "real",
                "cano": "2",
                "acnt_prdt_cd": "03",
                "markets": ["KR"],
                "mode": "approve",
                "asset": "fo",
            }
        ],
    )
    r = CliRunner().invoke(app, ["accounts", "show"])
    assert r.exit_code == 0, r.stdout
    line = next(x for x in r.stdout.splitlines() if x.startswith("real-fo"))
    assert "off" in line and "approve" not in line.split("—")[0]


def test_ping_counts_missing_keys_as_failures(project):
    _accounts(project)
    r = CliRunner().invoke(app, ["accounts", "ping"])
    assert (
        r.exit_code == 1
        and "[실패] 시세 클라이언트" in r.stdout
        and "paper-main" in r.stdout
        and "실패 3건" in r.stdout
    )


def test_ping_account_filter(project, monkeypatch):
    _accounts(project)
    from trader.features.accounts import cli as acli
    from trader.kernel.broker.fake import FakeMarket

    monkeypatch.setattr(
        acli,
        "build_market",
        lambda s, a: FakeMarket(prices={"005930": Decimal("71500"), "AAPL": Decimal("227.55")}),
    )
    monkeypatch.setattr(acli, "build_brokers", lambda s, a, **kw: [])
    r = CliRunner().invoke(app, ["accounts", "ping", "--account", "nope"])
    assert r.exit_code == 1 and "찾을 수 없습니다" in (r.stdout + (r.stderr or ""))
    r = CliRunner().invoke(app, ["accounts", "ping", "--account", "paper-main"])
    assert (
        "real-main" not in r.stdout and "[OK] 005930 현재가" in r.stdout and "실패 1건" in r.stdout
    )


# --- ping 파생 잔고 줄·order-test (M12 T18, P16) — 가짜 시세·브로커만, 네트워크 없음 ----------


def _fo_balance():
    from datetime import UTC, datetime

    from trader.kernel.broker.models import FoBalance, FoCash

    t = datetime(2026, 11, 5, 1, 0, tzinfo=UTC)
    z = Decimal("0")
    cash = FoCash(
        deposit=Decimal("10000000"), orderable_cash=Decimal("9000000"),
        orderable_total=Decimal("9000000"), margin_total=z, add_margin=z,
        nav=Decimal("10000000"), realized_today=z, unrealized=z, requested_at=t, received_at=t,
    )  # fmt: skip
    return FoBalance(positions=[], unverified=[], cash=cash)


def test_ping_includes_fo_balance_line(project, monkeypatch):
    """파생 계좌는 `settings.fo` 상품 설정으로 파생 브로커를 조립해 잔고 줄을 보인다."""
    _accounts(
        project,
        extra=[{"id": "paper-fo", "env": "paper", "cano": "2", "acnt_prdt_cd": "03",
                "markets": ["KR"], "mode": "off", "asset": "fo"}],
    )  # fmt: skip
    from trader.features.accounts import cli as acli
    from trader.kernel.broker.fake import FakeBroker, FakeFoBroker, FakeMarket
    from trader.kernel.broker.models import Cash

    monkeypatch.setattr(
        acli,
        "build_market",
        lambda s, a: FakeMarket(prices={"005930": Decimal("71500"), "AAPL": Decimal("227.55")}),
    )
    seen = []

    def brokers(secrets, accounts, *, fo_product=None):
        seen.append(([a.id for a in accounts], fo_product is not None))
        out = []
        for a in accounts:
            ba = acli.to_broker_account(a)
            if a.asset == "fo":
                assert fo_product is not None  # 파생은 상품 설정이 있어야 조립된다
                out.append(FakeFoBroker(ba, balance=_fo_balance()))
            else:
                cash = Cash(currency="KRW", available=Decimal("1000000"), total=Decimal("1000000"))
                out.append(FakeBroker(ba, cash={"KR": cash}))
        return out

    monkeypatch.setattr(acli, "build_brokers", brokers)
    r = CliRunner().invoke(app, ["accounts", "ping"])
    assert "[OK] paper-fo 파생 잔고: NAV 10000000 KRW, 주문가능 9000000 KRW" in r.stdout, r.stdout
    assert "[OK] paper-main KR 현금" in r.stdout and "실패 0건" in r.stdout and r.exit_code == 0
    assert (["paper-fo"], True) in seen


class _OrderTestMarket:
    def __init__(self, price):
        self.price, self.asked = price, []

    def get_price_kr(self, ticker):
        from types import SimpleNamespace

        self.asked.append(ticker)
        return SimpleNamespace(price=self.price)


def _order_test_env(project, monkeypatch, *, fail=()):
    """paper-main(KR) 계좌 + 가짜 시세(71,500) + 가짜 브로커. 브로커를 돌려준다."""
    _accounts(project)
    from trader.features.accounts import cli as acli
    from trader.kernel.broker.fake import FakeBroker

    holder = {}

    def brokers(secrets, accounts, **kw):
        out = [FakeBroker(acli.to_broker_account(a), fail=set(fail)) for a in accounts]
        holder["brokers"] = out
        return out

    monkeypatch.setattr(acli, "build_market", lambda s, a: _OrderTestMarket(Decimal("71500")))
    monkeypatch.setattr(acli, "build_brokers", brokers)
    return holder


def test_order_test_paper_places_and_cancels(project, monkeypatch):
    """모의 계좌: 1주 지정가(현재가 −10%, 호가단위 정렬) 매수 접수 → 즉시 취소, JSON 1줄."""
    import json

    holder = _order_test_env(project, monkeypatch)
    r = CliRunner().invoke(app, ["accounts", "order-test", "--account", "paper-main"])
    assert r.exit_code == 0, r.output
    out = json.loads(r.stdout.strip().splitlines()[-1])
    # 71500 × 0.9 = 64350 → 5만~20만 구간 호가단위 100 → 64400(가장 가까운 호가)
    assert out == {
        "account": "paper-main", "market": "KR", "ticker": "005930", "price": "64400",
        "order_id": "FAKE-1", "placed": True, "cancelled": True, "cancel_error": None,
    }  # fmt: skip
    (broker,) = holder["brokers"]
    (req,) = broker.orders
    assert (req.side, req.qty, req.order_type, req.price) == ("BUY", 1, "LIMIT", Decimal("64400"))
    assert broker.cancelled == ["FAKE-1"]
    assert broker.cancel_kwargs[0]["org_no"] == "00000" and broker.cancel_kwargs[0]["qty"] == 1


def test_order_test_refuses_real(project, monkeypatch):
    holder = _order_test_env(project, monkeypatch)
    r = CliRunner().invoke(app, ["accounts", "order-test", "--account", "real-main"])
    assert r.exit_code == 1 and "모의 계좌에서만" in (r.stdout + (r.stderr or ""))
    assert "brokers" not in holder  # 브로커를 조립하지도 않는다
    r = CliRunner().invoke(app, ["accounts", "order-test", "--account", "nope"])
    assert r.exit_code == 1 and "찾을 수 없습니다" in (r.stdout + (r.stderr or ""))


def test_order_test_cancels_even_if_cancel_fails(project, monkeypatch):
    """취소가 실패해도 결과 JSON 에 담고 exit 0(접수는 됐다). 접수 실패는 exit 1."""
    import json

    _order_test_env(project, monkeypatch, fail={"cancel_order"})
    r = CliRunner().invoke(app, ["accounts", "order-test", "--account", "paper-main"])
    assert r.exit_code == 0, r.output
    out = json.loads(r.stdout.strip().splitlines()[-1])
    assert out["placed"] is True and out["cancelled"] is False
    assert "KisApiError" in out["cancel_error"] and "가짜 실패 주입" in out["cancel_error"]
    _order_test_env(project, monkeypatch, fail={"place_order"})
    r = CliRunner().invoke(app, ["accounts", "order-test", "--account", "paper-main"])
    assert r.exit_code == 1
    out = json.loads(r.stdout.strip().splitlines()[-1])
    assert out["placed"] is False and out["cancelled"] is False and out["order_id"] is None
