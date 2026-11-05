"""evaluation `/daily` 명령·status 「성과」 절·일일 리포트/모의 체결 점검 잡."""

from datetime import UTC, date, datetime
from decimal import Decimal
from types import SimpleNamespace

from tests.features.accounts.conftest import make_account
from tests.features.ml.helpers import trading_days
from tests.kernel.daemon.test_runner import make_deps
from trader.features.evaluation import jobs, status, telegram
from trader.features.evaluation.feature import FEATURE
from trader.kernel.cli.boot import build_registry
from trader.kernel.config.loader import load
from trader.kernel.daemon import jobs as daemon_jobs
from trader.kernel.execution import ledger as repo
from trader.kernel.notify import commands
from trader.kernel.registry import discover

PAPER = make_account("paper-main", "paper", "auto", ["KR"])
NOW = datetime(2026, 10, 27, 8, 0, tzinfo=UTC)  # 화 17:00 KST


class Sent:
    def __init__(self):
        self.msgs = []

    def send(self, text, *, escape=True):
        self.msgs.append(text)
        return True


def _ctx(conn, sent):
    return commands.CommandContext(
        conn=conn, settings=None, state=None, now=lambda: NOW, notifier=sent,
        features=[FEATURE],
    )  # fmt: skip


def _nav(conn, d, nav, market="KR"):
    repo.snapshot_positions(
        conn, account_id="paper-main", d=d, nav=Decimal(nav), cash=Decimal(0), positions=[],
        market=market,
    )  # fmt: skip


def test_daily_command_without_snapshot_replies_hint(conn, monkeypatch):
    monkeypatch.setattr(telegram, "load_accounts", lambda: [PAPER])
    out = telegram.handle_daily(_ctx(conn, Sent()), None)
    assert out.startswith("[일일 리포트]") and "오늘 잔고 스냅샷이 아직 없어요" in out


def test_daily_command_renders_report(conn, monkeypatch):
    monkeypatch.setattr(telegram, "load_accounts", lambda: [PAPER])
    _nav(conn, date(2026, 10, 26), 10_000_000)
    _nav(conn, date(2026, 10, 27), 10_100_000)
    out = telegram.handle_daily(_ctx(conn, Sent()), None)
    assert "paper-main KR" in out and "+1.00%" in out
    assert telegram.DAILY.name == "daily" and telegram.DAILY in FEATURE.telegram_commands
    sent = Sent()
    ctx = _ctx(conn, sent)
    assert commands.registry([FEATURE])["daily"].handler(ctx, None) == out


def test_status_section_perf_lines(conn, monkeypatch):
    monkeypatch.setattr(status, "load_accounts", lambda: [PAPER])
    for d, v in zip(trading_days(3, start=date(2026, 10, 20)), (100, 110, 99), strict=True):
        _nav(conn, d, v)
    lines = status.render(_ctx(conn, Sent()))
    assert lines[0] == "- paper-main KR: 30일 -1.00% · MDD -10.00%"
    assert "rl_sizer 없음" in lines[1]
    assert status.STATUS_SECTION.title == "성과"
    assert status.STATUS_SECTION in FEATURE.status_sections
    assert "paper-main KR: 30일" in commands.status_message(_ctx(conn, Sent()))


def test_jobs_registered(project):
    schedule = load("schedule", build_registry(discover()), path=project / "config" / "x.yaml")
    ctx = SimpleNamespace(gates={"market": lambda job: None})
    got = {
        j.id: j
        for j in daemon_jobs.build(
            [FEATURE], schedule=schedule, settings=None, markets=["KR", "US"], ctx=ctx
        )
    }
    assert set(got) == {"kr_daily_report", "us_daily_report", "paper_review", "reflect", "rl_train"}
    assert str(got["us_daily_report"].trigger_kwargs["timezone"]) == "America/New_York"
    assert got["kr_daily_report"].cron["day_of_week"] == "mon-fri"
    assert got["paper_review"].cron == {"hour": 9, "minute": 0, "day_of_week": "sat"}
    assert got["paper_review"].spec is jobs.PAPER_REVIEW
    assert (got["kr_daily_report"].spec.gate, got["paper_review"].spec.gate) == ("market", "none")


def test_daily_report_body_and_paper_review_body(project):
    sent = Sent()
    deps = make_deps(
        project, now=lambda: NOW, notifier=sent,
        ports={"accounts": SimpleNamespace(accounts=lambda: [PAPER])},
    )  # fmt: skip
    assert jobs.daily_report_body(deps, SimpleNamespace(market="KR")) == {"items": 0}
    assert sent.msgs == []
    assert jobs.paper_review_body(deps) == {"items": 1, "alerts": 0} and sent.msgs == []
    conn = deps.conn_factory()
    try:
        _nav(conn, date(2026, 10, 27), 10_000_000)
        conn.execute(
            "INSERT INTO decisions(date, market, ticker, action, target_weight, confidence, "
            " data_json, created_at) VALUES ('2026-10-26','KR','005930','BUY',0.1,0.7,'{}',"
            "'2026-10-26T01:00:00Z')"
        )
        repo.insert_order(
            conn, decision_id=1, account_id="paper-main", ticker="005930", market="KR",
            side="BUY", qty=10, order_type="limit", price=Decimal(10000), status="expired",
            order_no=None, message="", mode="auto", created_at="2026-10-26T01:00:00+00:00",
        )  # fmt: skip
    finally:
        conn.close()
    assert jobs.daily_report_body(deps, SimpleNamespace(market="KR")) == {"items": 1}
    assert sent.msgs[-1].startswith("[일일 리포트]")
    jobs.paper_review_body(deps)
    assert sent.msgs[-1].startswith("[모의 체결 점검]")


def _decision(conn, d, ticker, action):
    conn.execute(
        "INSERT INTO decisions(date, market, ticker, action, data_json, created_at) "
        "VALUES (?, 'KR', ?, ?, '{}', 't')",
        (d, ticker, action),
    )


def _turn(conn, ticker, side, stance, conviction):
    conn.execute(
        "INSERT INTO debate_turns(decision_date, market, ticker, round, side, stance, conviction, "
        "data_json, created_at) VALUES ('2026-10-26', 'KR', ?, 0, ?, ?, ?, '{}', 't')",
        (ticker, side, stance, conviction),
    )


def test_status_shows_distribution(conn):
    ctx = _ctx(conn, Sent())
    assert [s.title for s in FEATURE.status_sections] == ["성과", "결정 분포(30일)"]
    assert status.distribution_lines(ctx) == []
    for i, action in enumerate(["BUY", "BUY", "HOLD", "SELL"]):
        _decision(conn, "2026-10-26", f"00593{i}", action)
    _decision(conn, "2026-09-01", "000660", "BUY")  # 30일 밖
    conn.commit()
    assert status.distribution_lines(ctx) == ["- BUY 2 · HOLD 1 · SELL 1"]
    _turn(conn, "005930", "bull", "BULLISH", 0.6)
    _turn(conn, "005930", "bear", "BULLISH", 0.5)  # 수렴
    _turn(conn, "005931", "bull", "BULLISH", 0.9)
    _turn(conn, "005931", "bear", "BEARISH", 0.9)  # 불수렴
    conn.commit()
    assert status.distribution_lines(ctx) == ["- BUY 2 · HOLD 1 · SELL 1 · 수렴율 50%"]
