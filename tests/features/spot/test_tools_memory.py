"""spot 도구 ↔ `ports["evaluation"]`: trader_context 의 memory·calibration, `35_calibration`,
submit-decision 의 `memory_entry_id` (옛 `orchestrator/tools.py` 메모리·보정 부분)."""

import pytest

from tests.features.evaluation.helpers import seed_matured
from tests.features.spot.conftest import T0, D, make_account
from tests.features.spot.helpers import ok_decision
from tests.features.spot.test_tools import _cash_brokers, _inputs, _ready_for_submit
from trader.features.accounts import api as accounts_api
from trader.features.spot import tools
from trader.kernel.session.run_store import read_checkpoint, run_dir


@pytest.fixture
def ctx(project, conn, settings):
    accounts = [make_account("paper-main", "paper", "auto", markets=["KR"])]
    return tools.ToolContext(
        settings=settings,
        accounts=accounts,
        conn=conn,
        state=project / "state",
        now=lambda: T0,
        policy=accounts_api.effective_mode,
        paused=accounts_api.is_paused,
    )


def _ml_inject(project):
    (project / "config" / "ml.yaml").write_text(
        "models:\n  calibration:\n    enabled: true\n    inject: true\n", encoding="utf-8"
    )


def test_trader_context_has_memory_and_calibration_keys_without_port(ctx, project):
    _inputs(project)
    got = tools.trader_context(ctx, "KR", D, "005930", {})
    assert got["memory"] == []
    assert got["memory_status"] == {"warmup": True, "breaker": None}
    assert got["calibration"] is None


def test_trader_context_memory_from_port(ctx, project, conn, ports, ml_port):
    seed_matured(conn, 60, now=T0)
    ports["ml"] = ml_port  # 피처 행 {"ret_5d": 0.01}
    ctx.ports = ports
    inputs = {"meta": {"ticker": "005930", "market": "KR"}}
    got = tools.trader_context(ctx, "KR", D, "005930", inputs)
    assert got["memory"] or got["memory_status"]["warmup"] is False
    assert got["memory_status"]["warmup"] is False and "error" not in got["memory_status"]
    assert got["calibration"] is None  # inject 기본 false


def test_ensure_calibration_writes_checkpoint_when_injected(ctx, project, conn, ports):
    seed_matured(conn, 60, now=T0)
    ctx.ports = ports
    assert tools.ensure_calibration(ctx, ticker="005930", market="KR", date=D) is None  # 기본
    assert read_checkpoint(run_dir(D, "KR", "005930"), "35_calibration") is None
    _ml_inject(project)
    data = tools.ensure_calibration(ctx, ticker="005930", market="KR", date=D)
    assert data is not None and data["n_decisions"] == 60
    saved = read_checkpoint(run_dir(D, "KR", "005930"), "35_calibration")
    assert saved["call"]["agent"] == "calibration" and saved["data"] == data
    assert tools.ensure_calibration(ctx, ticker="005930", market="KR", date=D) == data  # 그대로
    _inputs(project)
    assert tools.trader_context(ctx, "KR", D, "005930", {})["calibration"] == data
    ctx.ports = {}
    assert tools.ensure_calibration(ctx, ticker="005930", market="KR", date=D) is None  # 포트 없음


def test_submit_decision_returns_memory_entry_id(ctx, project, conn, ports):
    ctx.ports = ports
    _ready_for_submit(ctx, project)
    ctx.brokers = _cash_brokers(ctx)
    ctx.dry_run = False
    r = tools.submit_decision(ctx, ticker="005930", market="KR", date=D, payload=ok_decision())
    assert r["ok"] and isinstance(r["memory_entry_id"], int)
    row = conn.execute("SELECT id, round, ticker FROM memory_entries").fetchone()
    assert (row["id"], row["round"], row["ticker"]) == (r["memory_entry_id"], 0, "005930")
