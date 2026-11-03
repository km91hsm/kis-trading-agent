"""실전 전환 준비도 점검: 항목·임계값·모드 불변."""

import json
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest
from typer.testing import CliRunner

from tests.features.accounts.conftest import make_account
from tests.features.evaluation.helpers import seed_matured
from trader.features.evaluation import readiness
from trader.kernel.cli.main import app
from trader.kernel.execution import ledger as repo
from trader.kernel.ledger import runs
from trader.kernel.session.agents import ORCHESTRATOR

TODAY = date(2026, 10, 17)
INTERVALS = frozenset({"kr_monitor", "us_monitor", "fills_sync", "telegram_poll"})


def _runs(conn, n_ok: int, n_err: int = 0, kind="analyze", model="claude-opus"):
    for i in range(n_ok + n_err):
        d = TODAY - timedelta(days=i + 1)
        started = datetime(d.year, d.month, d.day, 1, tzinfo=UTC).isoformat()
        status = "ok" if i < n_ok else "error"
        rid = runs.start(conn, kind, "KR")
        conn.execute(
            "UPDATE runs SET started_at=?, finished_at=?, status=? WHERE id=?",
            (started, started, status, rid),
        )
        conn.execute(
            "INSERT INTO agent_calls(run_id, agent, model, status, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (rid, ORCHESTRATOR, model, status, started),
        )
    conn.commit()


def _navs(conn, values, account="paper-main"):
    for i, v in enumerate(values):
        repo.snapshot_positions(
            conn,
            account_id=account,
            d=TODAY - timedelta(days=len(values) - i),
            market="KR",
            nav=Decimal(str(v)),
            cash=Decimal(1),
            positions=[],
        )


def _shadow_pairs(conn, diffs, start=date(2026, 9, 1), market="KR"):
    for i, dlt in enumerate(diffs):
        day = (start + timedelta(days=i)).isoformat()
        for strat, ret in (("llm", 0.01 + dlt), ("candidates_1n", 0.01)):
            conn.execute(
                "INSERT INTO shadow_portfolios(market, date, strategy, weights_json, created_at, "
                "fwd_ret, matured_at, def_version) VALUES (?, ?, ?, ?, ?, ?, ?, 2)",
                (market, day, strat, '{"A": 1.0}', day, ret, day),
            )
    conn.commit()


def _check(conn, project, accounts, **kw):
    return readiness.check(conn, accounts=accounts, state=project / "state", today=TODAY, **kw)


def test_llm_alpha_needs_enough_pairs_and_positive_ci(project, conn):
    accounts = [make_account("paper-main", "paper", "auto")]

    def item():
        return next(i for i in _check(conn, project, accounts)["items"] if i["key"] == "llm_alpha")

    assert item()["ok"] is False and "0쌍" in item()["note"]
    _shadow_pairs(conn, [0.01] * (readiness.MIN_SHADOW_PAIRS - 1))
    assert item()["ok"] is False  # 쌍 부족
    _shadow_pairs(conn, [0.01], start=date(2026, 12, 1))
    assert item()["ok"] is True
    _shadow_pairs(conn, [-0.05] * 20, start=date(2027, 1, 1))  # 열위가 섞이면 하한이 0 아래
    assert item()["ok"] is False


def test_llm_alpha_note_with_few_pairs_has_no_interval(project, conn):
    accounts = [make_account("paper-main", "paper", "auto")]
    _shadow_pairs(conn, [0.01] * 3)
    it = next(i for i in _check(conn, project, accounts)["items"] if i["key"] == "llm_alpha")
    assert it["ok"] is False and "3쌍" in it["note"] and "구간 없음" in it["note"]


def test_readiness_items_and_thresholds(project, conn):
    accounts = [
        make_account("paper-main", "paper", "auto"),
        make_account("real-main", "real", "off"),
    ]
    r = _check(conn, project, accounts)
    keys = {i["key"] for i in r["items"]}
    assert {
        "paper_days",
        "run_error_rate",
        "order_failed_rate",
        "return_vs_benchmark",
        "llm_alpha",
        "mdd",
        "calibration_active",
        "stale_approvals",
    } <= keys
    assert r["ok"] is False
    assert next(i for i in r["items"] if i["key"] == "paper_days")["value"] == 0

    _runs(conn, 22, n_err=1)
    _navs(conn, [100, 101, 102, 101, 103, 104])
    seed_matured(conn, 60)
    _shadow_pairs(conn, [0.004 + 0.001 * (i % 3) for i in range(readiness.MIN_SHADOW_PAIRS)])
    r = _check(conn, project, accounts)
    by = {i["key"]: i for i in r["items"]}
    assert by["paper_days"]["ok"] and by["paper_days"]["value"] == 22
    assert by["run_error_rate"]["ok"] and by["run_error_rate"]["value"] < 0.1
    assert by["order_failed_rate"]["ok"]
    # 파생 레인 주문 실패는 현물 실전 전환 준비도에 섞지 않는다
    for i in range(5):
        repo.insert_order(conn, decision_id=None, account_id="paper-fo", ticker=f"A0561{i}",
                          market="KR", side="BUY", qty=1, order_type="LIMIT", price=None,
                          status="failed", order_no=None, message="fo", mode="auto",
                          created_at=f"{TODAY.isoformat()}T01:00:00+00:00", asset="fo",
                          origin="fo")  # fmt: skip
    again = _check(conn, project, accounts)
    assert next(i for i in again["items"] if i["key"] == "order_failed_rate")["ok"]
    assert by["return_vs_benchmark"]["ok"] and by["return_vs_benchmark"]["note"].startswith("정보")
    assert by["llm_alpha"]["ok"] and by["llm_alpha"]["value"] > 0
    assert by["mdd"]["ok"] and by["mdd"]["value"] < 0
    assert by["calibration_active"]["value"] is True
    assert by["stale_approvals"]["ok"] and by["stale_approvals"]["value"] == 0
    assert r["ok"] is True

    # 만료된 미결 승인 → 불합격
    repo.create_approval(
        conn,
        decision_id=1,
        account_id="paper-main",
        order={},
        requested_at="2026-10-01T00:00:00+00:00",
        expires_at="2026-10-01T00:30:00+00:00",
        fingerprint="fp",
    )
    r = _check(conn, project, accounts)
    assert next(i for i in r["items"] if i["key"] == "stale_approvals")["ok"] is False
    assert r["ok"] is False


def test_readiness_never_changes_modes(project, conn):
    accounts = [
        make_account("paper-main", "paper", "auto"),
        make_account("real-main", "real", "off"),
    ]
    _runs(conn, 25)
    _navs(conn, [100, 102, 104])
    seed_matured(conn, 60)
    _shadow_pairs(conn, [0.005] * readiness.MIN_SHADOW_PAIRS)
    before = conn.execute("SELECT COUNT(*) FROM control").fetchone()[0]
    r = _check(conn, project, accounts)
    assert r["ok"] is True
    assert conn.execute("SELECT COUNT(*) FROM control").fetchone()[0] == before
    assert conn.execute("SELECT COUNT(*) FROM control WHERE key LIKE 'mode:%'").fetchone()[0] == 0


def test_cli_backtest_and_readiness(project, conn):
    seed_matured(conn, 5, start=date(2026, 3, 2), hit_rate=1.0)
    conn.commit()
    r = CliRunner().invoke(app, ["evaluation", "backtest", "--since", "2026-03-01"])
    assert r.exit_code == 0, r.stdout
    out = json.loads(r.stdout.strip().splitlines()[-1])
    assert out["n"] == 5 and out["hit_rate"] == 1.0
    r = CliRunner().invoke(app, ["evaluation", "readiness"])
    assert r.exit_code == 0, r.stdout
    out = json.loads(r.stdout.strip().splitlines()[-1])
    assert out["ok"] is False and any(i["key"] == "paper_days" for i in out["items"])


def test_cli_backtest_bad_date(project, conn):
    r = CliRunner().invoke(app, ["evaluation", "backtest", "--since", "nope"])
    assert r.exit_code == 1


def test_paper_days_excludes_fake_sessions(project, conn):
    _runs(conn, 25, model="fake")
    r = _check(conn, project, [make_account("paper-main", "paper", "auto")])
    assert next(i for i in r["items"] if i["key"] == "paper_days")["value"] == 0


def test_stale_approvals_ignores_unexpired(project, conn):
    far = (datetime.now(UTC) + timedelta(minutes=20)).isoformat()
    repo.create_approval(
        conn, decision_id=1, account_id="paper-main", order={}, requested_at=far,
        expires_at=far, fingerprint="fp",
    )  # fmt: skip
    r = readiness.check(
        conn,
        accounts=[make_account("paper-main", "paper", "auto")],
        state=project / "state",
        today=datetime.now(UTC).date(),
    )
    item = next(i for i in r["items"] if i["key"] == "stale_approvals")
    assert item["ok"] and item["value"] == 0


def test_run_error_rate_ignores_interval_jobs(conn, project):
    _runs(conn, 9, 1)  # cron 잡(analyze): ok 9 + error 1
    _runs(conn, 0, 5, kind="kr_monitor")
    _runs(conn, 0, 2, kind="fills_sync")
    _runs(conn, 0, 1, kind="telegram_poll")
    _runs(conn, 10, 0, kind="monitor")  # 재평가 세션은 ok 도 기록되니 포함
    r = _check(conn, project, [], interval_kinds=INTERVALS)
    item = next(i for i in r["items"] if i["key"] == "run_error_rate")
    assert item["value"] == pytest.approx(1 / 20)


def test_interval_kinds_of_collects_kind_and_expanded_id():
    from trader.kernel.registry import discover

    kinds = readiness.interval_kinds_of(discover())
    assert {"kr_monitor", "us_monitor"} <= kinds


def test_llm_alpha_judged_per_market_of_live_accounts(project, conn):
    accounts = [
        make_account("paper-main", "paper", "auto", ["KR", "US"]),
        make_account("real-main", "real", "off", ["KR", "US"]),
    ]

    def item():
        return next(i for i in _check(conn, project, accounts)["items"] if i["key"] == "llm_alpha")

    _shadow_pairs(conn, [0.004 + 0.001 * (i % 3) for i in range(readiness.MIN_SHADOW_PAIRS)])
    it = item()
    assert it["ok"] is False and "US" in it["note"] and "KR" in it["note"]
    _shadow_pairs(
        conn, [0.004 + 0.001 * (i % 3) for i in range(readiness.MIN_SHADOW_PAIRS)], market="US"
    )
    assert item()["ok"] is True
