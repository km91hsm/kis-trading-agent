"""`trader fo …` CLI(옛 `tests/test_cli_fo.py` 의 fo 표면 재작성).

부트는 커널 `boot` 를 그대로 쓰되 `ports` 만 가짜(`FakeFoBroker`·`FakeMarket`)로 바꿔 끼운다 —
네트워크·실주문 없음. 옛 테스트 중 브로커 조립(`_build_brokers`)·`kis ping`·시세 키 폴백·
readiness·control/status 계좌 가시성·`_session_brokers` 는 옛 모듈 내부 단언이라 옮기지 않았다
(각각 accounts 기능 테스트가 맡는다)."""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace

import pytest
from typer.testing import CliRunner

from tests.features.accounts.conftest import make_account
from tests.features.fo._support import _bal, _row
from trader.features.fo import cli, engine, store
from trader.kernel.broker import fo_master
from trader.kernel.broker.fake import FakeFoBroker, FakeMarket
from trader.kernel.broker.fo import CONTRACT_VERSION
from trader.kernel.broker.models import FoBalance, FoCash, FoPosition, FoQuote
from trader.kernel.cli import boot as boot_mod
from trader.kernel.cli.main import app
from trader.kernel.ledger import db
from trader.kernel.registry import discover
from trader.kernel.registry.contracts import all_migrations

T0 = datetime(2026, 10, 27, 1, 15, 30, tzinfo=UTC)
ACCT = "paper-fo"


def _cash(nav: Decimal | None = Decimal("10000000")) -> FoCash:
    return FoCash(
        deposit=Decimal("10000000"), orderable_cash=Decimal("8000000"),
        orderable_total=Decimal("8000000"), margin_total=Decimal(0), add_margin=Decimal(0),
        nav=nav, realized_today=Decimal(0), unrealized=Decimal(0),
        requested_at=T0, received_at=T0,
    )  # fmt: skip


def _pos(code: str, kind: str, side: str) -> FoPosition:
    return FoPosition(
        code=code, name=None, kind=kind, contract_side=side, qty=1, avg_price=Decimal(1),
        current_price=Decimal(1), pnl=Decimal(0), sellable_qty=1,
    )  # fmt: skip


def _quote(code: str, kind: str = "FUTURES", **over) -> FoQuote:
    base = dict(
        code=code, kind=kind, price=Decimal("350.5"), bid=Decimal("350.48"),
        ask=Decimal("350.52"), change_pct=0.0, volume=1, open_interest=1, basis=Decimal("0.7"),
        upper=Decimal(380), lower=Decimal(320), last_trade_date=None, days_left=16,
        hist_vol=15.0, iv=None, delta=None, gamma=None, theta=None, vega=None, strike=None,
        underlying=Decimal("349.8"), underlying_asof=T0, quote_asof=T0, received_at=T0,
    )  # fmt: skip
    return FoQuote(**{**base, **over})


class Env:
    """가짜 포트 묶음 + 테스트가 들여다볼 브로커·시세."""

    def __init__(self, *, env="paper", balance=None, market=None):
        self.fo = make_account(f"{env}-fo", env, "auto" if env == "paper" else "off", asset="fo")
        self.spot = make_account("paper-main", "paper", "auto", ["KR"])
        self.broker = FakeFoBroker(
            self.fo, balance=balance or FoBalance(positions=[], unverified=[], cash=_cash()),
            now=lambda: T0,
        )  # fmt: skip
        self.market = market or FakeMarket(
            prices={}, fo_quotes={"105W11": _quote("105W11")},
            kospi200=(Decimal("349.8"), T0),
        )  # fmt: skip

    def ports(self, settings):
        from trader.features.accounts import api as accounts_api
        from trader.features.regime import ports as regime_ports

        acc = SimpleNamespace(
            fo_accounts=lambda: [self.fo], fo_brokers=lambda: {self.fo.id: self.broker},
            accounts=lambda: [self.spot], brokers=list, market_client=lambda: self.market,
            policy=accounts_api.account_mode, paused=accounts_api.is_paused,
            holiday_fn=lambda: (lambda m, d: False),
        )  # fmt: skip
        regime = regime_ports.build_port(SimpleNamespace(settings=settings))
        return {"accounts": acc, "regime": regime}


@pytest.fixture
def sample(master, monkeypatch):
    """마스터 로더를 합성 표본으로 고정(네트워크 없음)."""
    monkeypatch.setattr(fo_master, "load", lambda **kw: list(master))
    return master


@pytest.fixture
def install(project, monkeypatch, sample):
    """`install(env)` — CLI 부트의 포트를 가짜로 바꾼다."""
    real = boot_mod.boot

    def go(env: Env):
        def fake_boot(**kw):
            b = real(**{**kw, "ports": False, "settings": True, "conn": True, "secrets": True})
            return replace(b, ports=env.ports(b.settings)) if kw.get("ports") else b

        monkeypatch.setattr(cli, "boot", fake_boot)
        return env

    return go


def _run(*args: str):
    return CliRunner().invoke(app, ["fo", *args])


def _last_json(r) -> dict:
    return json.loads(r.stdout.strip().splitlines()[-1])


def _flags() -> dict[str, int]:
    conn = db.connect()
    try:
        rows = conn.execute("SELECT key, value FROM control WHERE key LIKE 'fo_verified:%'")
        return {k: json.loads(v) for k, v in rows.fetchall()}
    finally:
        conn.close()


def test_fo_app_registered(project):
    assert "fo" in [f.id for f in discover() if f.cli is not None]
    r = _run("--help")
    assert r.exit_code == 0
    for name in ("contracts", "chain", "verify", "session", "order-test", "status", "monitor",
                 "adopt", "close"):  # fmt: skip
        assert name in r.stdout


def test_contracts_lists_front_month_and_strikes(project, sample):
    r = _run("contracts", "--today", "2026-10-27")
    assert r.exit_code == 0, r.stdout + (r.stderr or "")
    out = json.loads(r.stdout)
    assert out["product"] == "mini" and out["futures"]["front"]["code"] == "105W11"
    assert "105W12" in [c["code"] for c in out["futures"]["months"]]
    assert out["options"]["front"]["expiry_ym"] == "202611"
    assert Decimal("350") in {Decimal(s) for s in out["options"]["front"]["strikes"]}


def test_contracts_unknown_product(project, sample):
    r = _run("contracts", "--product", "nope")
    assert r.exit_code == 1 and "fo.products" in (r.stdout + (r.stderr or ""))


def test_chain_atm_window_and_errors(project, install, sample, monkeypatch):
    q = _quote("205W11350", "CALL", strike=Decimal(350), iv=18.0)
    env = install(Env(market=FakeMarket(prices={}, fo_quotes={"205W11350": q},
                                        kospi200=(Decimal("349.8"), T0))))  # fmt: skip
    r = _run("chain", "--today", "2026-10-27", "--strikes", "1")
    assert r.exit_code == 0, r.stdout + (r.stderr or "")
    out = json.loads(r.stdout)
    assert out["expiry_ym"] == "202611" and out["underlying"] == "349.8"
    assert len(out["strikes"]) <= 3 and Decimal("350") in {Decimal(k) for k in out["strikes"]}
    assert [x["code"] for x in out["quotes"]] == ["205W11350"]
    assert out["errors"] and all(c != "205W11350" for c in out["errors"])
    assert env.broker.orders == []
    stripped = [replace(c, strike=None) if c.kind != "FUTURES" else c for c in sample]
    monkeypatch.setattr(fo_master, "load", lambda **kw: stripped)
    r = _run("chain", "--today", "2026-10-27")
    assert r.exit_code == 1 and "행사가" in (r.stdout + (r.stderr or ""))
    assert "Traceback" not in (r.stdout + (r.stderr or ""))


def _verify(install, balance, *args, **env_kw):
    env = install(Env(balance=balance, **env_kw))
    r = _run("verify", "--account", ACCT, *args)
    return r, env


def test_verify_marks_only_verified_vehicles(project, install):
    # 빈 잔고: 조회는 성공해도 어떤 수단도 검증되지 않는다(빈 응답은 검증이 아니다)
    r, env = _verify(install, FoBalance(positions=[], unverified=[], cash=_cash()))
    assert r.exit_code == 0, r.stdout + (r.stderr or "")
    out = _last_json(r)
    assert out["verified"] == [] and _flags() == {}
    assert out["balance"]["positions"] == 0 and out["balance"]["nav"] is True
    assert out["quote"]["code"] == "105W11" and out["orderable"]["ord_qty"] >= 1
    assert env.broker.orders == [] and env.broker.cancelled == []  # 조회 전용

    bal = FoBalance(positions=[_pos("105W11", "FUTURES", "LONG"),
                               _pos("205W11350", "CALL", "SHORT")], unverified=[],
                    cash=_cash())  # fmt: skip
    r, _ = _verify(install, bal)
    assert _last_json(r)["verified"] == ["FUTURES", "OPTION_SELL"]
    assert _flags() == {
        "fo_verified:paper:paper-fo:mini:FUTURES": CONTRACT_VERSION,
        "fo_verified:paper:paper-fo:mini:OPTION_SELL": CONTRACT_VERSION,
    }


def test_verify_nav_missing_is_not_verified(project, install):
    bal = FoBalance(positions=[_pos("305W11347", "PUT", "LONG")], unverified=[],
                    cash=_cash(None))  # fmt: skip
    r, _ = _verify(install, bal, "--vehicle", "OPTION_BUY")
    assert _last_json(r)["verified"] == [] and _flags() == {}


def test_verify_vehicle_filter(project, install):
    bal = FoBalance(positions=[_pos("105W11", "FUTURES", "LONG"),
                               _pos("205W11350", "CALL", "LONG")], unverified=[],
                    cash=_cash())  # fmt: skip
    r, _ = _verify(install, bal, "--vehicle", "OPTION_BUY")
    assert _last_json(r)["verified"] == ["OPTION_BUY"]
    assert list(_flags()) == ["fo_verified:paper:paper-fo:mini:OPTION_BUY"]
    r, _ = _verify(install, bal, "--vehicle", "BOGUS")
    assert r.exit_code == 1


def test_verify_rejects_stock_and_unknown_account(project, install):
    install(Env())
    r = _run("verify", "--account", "paper-main")
    assert r.exit_code == 1 and "asset" in (r.stdout + (r.stderr or ""))
    r = _run("verify", "--account", "nobody")
    assert r.exit_code == 1 and "찾을 수 없습니다" in (r.stdout + (r.stderr or ""))


def test_verify_no_flag_when_a_step_fails(project, install):
    bal = FoBalance(positions=[_pos("105W11", "FUTURES", "LONG")], unverified=[], cash=_cash())
    market = FakeMarket(prices={}, fo_quotes={"105W11": _quote("105W11")},
                        kospi200=(Decimal("349.8"), T0), fail={"get_kospi200"})  # fmt: skip
    r, _ = _verify(install, bal, market=market)
    assert r.exit_code == 1
    out = _last_json(r)
    assert out["verified"] == [] and out["errors"] and _flags() == {}


def test_verify_no_flag_when_no_front_month(project, install, sample, monkeypatch):
    bal = FoBalance(positions=[_pos("105W11", "FUTURES", "LONG")], unverified=[], cash=_cash())
    no_futs = [c for c in sample if c.kind != "FUTURES"]
    monkeypatch.setattr(fo_master, "load", lambda **kw: no_futs)
    r, env = _verify(install, bal)
    assert r.exit_code == 1
    out = _last_json(r)
    assert out["front"] is None and any("front" in e for e in out["errors"])
    assert out["verified"] == [] and _flags() == {}
    assert "get_fo_price" not in env.market.calls  # 시세 단계는 돌지 않았다


def test_verify_no_flag_when_master_empty(project, install, monkeypatch):
    monkeypatch.setattr(fo_master, "load", lambda **kw: [])
    r, _ = _verify(install, FoBalance(positions=[], unverified=[], cash=_cash()))
    assert r.exit_code == 1
    assert any("master" in e for e in _last_json(r)["errors"]) and _flags() == {}


def test_order_test_refuses_real(project, install):
    env = install(Env(env="real"))
    r = _run("order-test", "--account", "real-fo", "--vehicle", "FUTURES")
    assert r.exit_code == 1 and "모의(paper) 계좌에서만" in (r.stdout + (r.stderr or ""))
    assert env.broker.orders == [] and env.broker.calls == []


def test_order_test_sets_flag_only_on_success(project, install, monkeypatch):
    install(Env())
    results = iter([{"ok": False, "errors": ["x"]}, {"ok": True, "errors": []}])
    monkeypatch.setattr(cli.fo_order_test, "run", lambda *a, **kw: next(results))
    args = ["order-test", "--account", ACCT, "--vehicle", "FUTURES"]
    assert _run(*args).exit_code == 1 and _flags() == {}
    r = _run(*args)
    assert r.exit_code == 0, r.stdout + (r.stderr or "")
    assert list(_flags()) == ["fo_verified:paper:paper-fo:mini:FUTURES"]
    assert _last_json(r)["verified_flag"].endswith(":FUTURES")


def test_status_prints_ledger_lines(project):
    r = _run("status")
    assert r.exit_code == 0 and "보유 없음" in r.stdout and "오늘 결정: 없음" in r.stdout


def test_session_dry_run_fake(project, install, monkeypatch):
    monkeypatch.setenv("FAKE_CLAUDE", "1")
    (project / "config" / "settings.yaml").write_text("fo:\n  enabled: true\n", encoding="utf-8")
    install(Env())
    conn = db.connect()
    db.init_schema(conn, all_migrations(discover()))
    conn.close()
    r = _run("session", "--dry-run", "--asof", "2026-10-27T10:30:00")
    assert r.exit_code == 0, r.stdout + (r.stderr or "")
    out = _last_json(r)
    assert out["dry_run"] is True and out["date"] == "2026-10-27"
    assert out["session"]["fake"] is True and out["decision"]["decision"]["intent"] == "HOLD"


def test_monitor_once_dry_run_ticks(project, install, monkeypatch):
    install(Env())
    seen = {}

    def tick(ctx, **kw):
        seen["dry_run"], seen["accounts"] = ctx.dry_run, [a.id for a in ctx.accounts]
        return {"accounts": {}}

    monkeypatch.setattr(engine, "tick", tick)
    r = _run("monitor", "--once", "--dry-run")
    assert r.exit_code == 0, r.stdout + (r.stderr or "")
    assert seen == {"dry_run": True, "accounts": [ACCT]}


def test_close_requires_confirm(project):
    r = _run("close", "--account", ACCT, "--position", "1")
    assert r.exit_code == 1 and "CLOSE" in (r.stdout + (r.stderr or ""))


def test_close_unknown_position(project, install):
    install(Env())
    r = _run("close", "--account", ACCT, "--position", "99", "--confirm", "CLOSE")
    assert r.exit_code == 1 and "포지션 99" in (r.stdout + (r.stderr or ""))


def test_adopt_unknown_to_held_only_when_balance_matches(project, install):
    install(Env(balance=_bal(_row(code="105W11", kind="FUTURES", side="LONG", qty=2))))
    conn = db.connect()
    db.init_schema(conn, all_migrations(discover()))
    with store.tx(conn):
        pid = store.insert_position(
            conn, account_id=ACCT, code="105W11", kind="FUTURES", contract_side="LONG",
            direction="LONG", vehicle="FUTURES", status="unknown", qty_target=2, qty_held=2,
            leverage_used=0.35, stop_pt=Decimal(345), nav_at_open=Decimal(10000000),
            decision_date="2026-10-27",
        )  # fmt: skip
    conn.close()
    base = ["adopt", "--account", ACCT, "--position", str(pid), "--side", "LONG",
            "--kind", "FUTURES", "--stop-pt", "345"]  # fmt: skip
    r = _run(*base, "--qty", "3")  # 수량 불일치 → 거부
    assert r.exit_code == 1 and _last_json(r) == {"position_id": pid, "adopted": False}
    r = _run(*base, "--qty", "2")
    assert r.exit_code == 0 and _last_json(r)["adopted"] is True
    conn = db.connect()
    assert store.get_position(conn, pid).status == "held"
    conn.close()
