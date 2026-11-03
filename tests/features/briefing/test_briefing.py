"""아침 브리핑(Stock docs/06): 입력 묶음·FAKE 응답 전송·감속·끔·잡 본문·잡 등록."""

from datetime import UTC, date, datetime
from decimal import Decimal
from types import SimpleNamespace

from tests.features.accounts.conftest import make_account
from tests.kernel.daemon.test_runner import make_deps
from trader.features.briefing import briefing, jobs
from trader.features.briefing.feature import FEATURE
from trader.features.briefing.settings import BriefingCfg
from trader.kernel.cli.boot import build_registry
from trader.kernel.config.loader import load
from trader.kernel.daemon import jobs as daemon_jobs
from trader.kernel.execution import ledger as repo
from trader.kernel.registry import discover

NOW = datetime(2026, 10, 26, 22, 20, tzinfo=UTC)  # 화 07:20 KST
ACCTS = [make_account("paper-main", "paper", "auto", ["KR"])]


class Sent:
    def __init__(self):
        self.msgs = []

    def send(self, text, *, escape=True):
        self.msgs.append(text)
        return True


def test_inputs_shape(conn, settings):
    repo.snapshot_positions(
        conn, account_id="paper-main", d=date(2026, 10, 26), nav=Decimal("1000"),
        cash=Decimal("700"), market="KR",
        positions=[{"ticker": "005930", "qty": 1, "avg_price": "0", "current_price": "0",
                    "eval_amount": "300"}],
    )  # fmt: skip
    p = briefing.build_inputs(conn, settings, ACCTS, now=NOW)
    assert set(p) == {
        "date", "us_overnight", "regime", "market_view", "news", "calendar", "holdings",
    }  # fmt: skip
    assert p["us_overnight"] is None  # 시세 클라이언트 없음
    assert p["holdings"] == [{"market": "KR", "ticker": "005930", "name": None, "weight": 0.3}]


def test_run_fake_sends_and_respects_switches(conn, settings, monkeypatch):
    monkeypatch.setenv("FAKE_CLAUDE", "1")
    sent = Sent()
    r = briefing.run(conn, settings, ACCTS, now=NOW, notifier=sent)
    assert r == {"lines": 1} and sent.msgs[-1].startswith("[아침 브리핑]")
    off = settings.model_copy(update={"briefing": BriefingCfg(enabled=False)})
    assert briefing.run(conn, off, ACCTS, now=NOW, notifier=sent) == {"skipped": "꺼짐"}
    monkeypatch.setattr(
        "trader.features.briefing.briefing.session_allowance",
        lambda *a, **k: (False, "구독 5시간 사용률 85%"),
    )
    assert briefing.run(conn, settings, ACCTS, now=NOW, notifier=sent)["skipped"].startswith("감속")


def test_briefing_uses_kst_date(conn, settings):
    p = briefing.build_inputs(conn, settings, ACCTS, now=NOW)  # 22:20Z = 다음 날 07:20 KST
    assert p["date"] == "2026-10-27"


def _deps(project, settings, sent, *, paused=False, holiday=False, markets=("KR",)):
    acc = SimpleNamespace(
        accounts=lambda: ACCTS, market_client=lambda: None, paused=lambda c: paused,
        holiday_fn=lambda: (lambda m, d: holiday),
    )  # fmt: skip
    return make_deps(
        project, settings=settings, now=lambda: NOW, notifier=sent, markets=list(markets),
        ports={"accounts": acc}, registry=build_registry(discover()),
    )  # fmt: skip


def test_briefing_body_skips_holiday_and_paused(project, settings, monkeypatch):
    monkeypatch.setenv("FAKE_CLAUDE", "1")
    sent = Sent()
    d = _deps(project, settings, sent, holiday=True)
    assert jobs.briefing_body(d) == {"skipped": "휴장일"}
    d = _deps(project, settings, sent, paused=True)
    assert jobs.briefing_body(d) == {"skipped": "일시정지"}
    d = _deps(project, settings, sent, markets=("US",))
    assert jobs.briefing_body(d) == {"skipped": "KR 시장 아님"}
    c = d.conn_factory()
    assert c.execute("SELECT COUNT(*) FROM agent_calls").fetchone()[0] == 0
    c.close()
    assert sent.msgs == []
    d = _deps(project, settings, sent)
    assert jobs.briefing_body(d) == {"lines": 1} and sent.msgs[-1].startswith("[아침 브리핑]")


def test_briefing_job_registered_and_disabled_absent(project, settings):
    schedule = load("schedule", build_registry(discover()), path=project / "config" / "x.yaml")

    def ids(s):
        return {
            j.id: j
            for j in daemon_jobs.build(
                [FEATURE], schedule=schedule, settings=s, markets=["KR"],
                ctx=SimpleNamespace(gates={}),
            )
        }  # fmt: skip

    got = ids(settings)
    assert got["morning_briefing"].cron == {"hour": 7, "minute": 20, "day_of_week": "mon-fri"}
    assert got["morning_briefing"].spec is jobs.MORNING_BRIEFING
    off = settings.model_copy(update={"briefing": BriefingCfg(enabled=False)})
    assert "morning_briefing" not in ids(off)
