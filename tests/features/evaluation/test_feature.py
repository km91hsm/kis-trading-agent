"""evaluation 기능 선언."""

import sqlite3
from pathlib import Path

import pytest

from trader.features.evaluation.feature import FEATURE
from trader.features.evaluation.settings import MemorySettings
from trader.kernel.cli.boot import build_registry
from trader.kernel.config.loader import load
from trader.kernel.platform.bundle import templates_dir
from trader.kernel.registry import discover


def test_registered_and_template_parity():
    assert FEATURE.id == "evaluation" and FEATURE.tier == "service"
    assert [m.version for m in FEATURE.migrations] == [13]
    reg = build_registry(discover())
    st = load("settings", reg, path=templates_dir() / "config" / "settings.yaml")
    assert isinstance(st.memory, MemorySettings) and st.memory.warmup == 50
    sc = load("schedule", reg, path=templates_dir() / "config" / "schedule.yaml")
    for key in ("reflect", "daily_report", "paper_review", "rl_train"):
        assert hasattr(sc, key), key
    assert sc.reflect == "16:30" and sc.rl_train.days == "sat"
    assert sc.daily_report.us.tz == "America/New_York"


def test_v13_creates_four_tables(conn):
    def cols(t):
        return {r[1] for r in conn.execute(f"PRAGMA table_info({t})")}

    assert {"decision_id", "round", "direction_score", "avoided_loss", "realized_pnl_pct",
            "post_fill_return", "skipped_debate", "held_at_decision", "price_return"} <= cols(
        "memory_entries"
    )
    assert not {"realized_return", "alpha_vs_benchmark"} & cols("memory_entries")
    assert {"n_decisions", "active", "json"} <= cols("calibration_runs")
    assert {"weights_json", "fwd_ret", "matured_at", "partial", "def_version"} <= cols(
        "shadow_portfolios"
    )
    rl_cols = {"decision_id", "model_version", "obs", "llm_weight", "proposed_weight", "applied"}
    assert rl_cols <= cols("rl_shadow")
    ins = "INSERT INTO rl_shadow(decision_id, account_id, created_at, model_version, obs, " \
          "llm_weight, proposed_weight) VALUES (1,'paper-main','t','v','[]',0.1,0.2)"
    conn.execute(ins)
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(ins)
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO shadow_portfolios(market, date, strategy, weights_json, created_at) "
            "VALUES ('KR','2026-10-01','x','{}','t')"
        )


def test_evaluation_does_not_import_spot():
    root = Path(__file__).resolve().parents[3] / "src" / "trader" / "features" / "evaluation"
    files = list(root.rglob("*.py"))
    assert files
    for f in files:
        assert "trader.features.spot" not in f.read_text(encoding="utf-8"), f
