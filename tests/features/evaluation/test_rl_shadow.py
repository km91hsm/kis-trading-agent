from decimal import Decimal

import pytest

from tests.features.evaluation.helpers import seed_matured
from tests.features.spot.helpers import ok_decision
from trader.features.evaluation.rl import shadow
from trader.features.evaluation.rl.dataset import OBS_COLUMNS
from trader.features.evaluation.rl.env import OBS_SIZE
from trader.features.evaluation.rl.policy import FixedPolicy
from trader.features.ml import api as ml_api
from trader.features.ml.api import ml_config
from trader.features.spot.rules import Portfolio
from trader.kernel.broker.models import Position


def test_load_requires_enabled_and_promoted(project):
    cfg = ml_config({"models": {"rl_sizer": {"enabled": True}}})
    assert shadow.load(cfg, None) is None  # 모델 없음
    v = ml_api.registry_save("rl_sizer", FixedPolicy(0.07), {"obs_columns": list(OBS_COLUMNS)})
    assert shadow.load(cfg, None) is None  # 미승격
    ml_api.registry_promote("rl_sizer", v, reason="t")
    s = shadow.load(cfg, None)
    assert s is not None and s.version == v and s.policy.act([0.0] * OBS_SIZE) == 0.07
    assert shadow.load(ml_config(), None) is None  # enabled False


def test_observe_position_and_drawdown():
    pf = Portfolio(
        account_id="p",
        market="KR",
        nav=Decimal(1000),
        cash_available=Decimal(500),
        positions={
            "005930": Position(
                ticker="005930",
                market="KR",
                qty=1,
                avg_price=Decimal(1),
                current_price=Decimal(1),
                eval_amount=Decimal(250),
                pnl_pct=0.0,
            )
        },
    )
    obs = shadow.observe(
        None,
        ticker="005930",
        market="KR",
        decision=ok_decision(),
        portfolio=pf,
        nav_hist=[("d1", Decimal(1200)), ("d2", Decimal(1000))],
    )
    assert len(obs) == OBS_SIZE and obs[12] == 0.6
    assert obs[13] == pytest.approx(ok_decision()["target_weight"])
    assert obs[14] == pytest.approx(0.25) and obs[15] == pytest.approx(-1 / 6)


def test_record_idempotent_and_recent_joins_outcome(conn):
    seed_matured(conn, 1)  # price_return 0.03
    dec_id = conn.execute("SELECT decision_id FROM memory_entries").fetchone()[0]
    kw = dict(
        decision_id=dec_id,
        account_id="paper-main",
        model_version="v",
        obs=[0.0] * OBS_SIZE,
        llm_weight=0.10,
        proposed_weight=0.20,
        applied=False,
    )
    assert shadow.record(conn, **kw) == shadow.record(conn, **kw)
    rows = shadow.recent(conn, "paper-main", days=30)
    assert len(rows) == 1 and rows[0]["realized_rl"] == pytest.approx(0.006)
    assert rows[0]["realized_llm"] == pytest.approx(0.003) and rows[0]["ticker"] == "T000"
    assert rows[0]["name"] is None  # 종목명(원장 ticker_names) — 화면이 코드+이름으로 그린다
    conn.execute(
        "INSERT INTO ticker_names(market, ticker, name, source, updated_at) VALUES(?,?,?,?,?)",
        ("KR", "T000", "테스트종목", "test", "2026-11-05T00:00:00+00:00"),
    )
    assert shadow.recent(conn, "paper-main", days=30)[0]["name"] == "테스트종목"
    assert shadow.recent(conn, "real-main") == []
