"""현물 회귀 스냅샷(옛 `test_stock_regression_fo.py` 이식)과 FAKE analyze 체크포인트 집합.

골든 갱신: `TRADER_UPDATE_GOLDEN=1 uv run pytest tests/features/spot/test_regression.py`."""

import json
import os
from pathlib import Path

from tests.features.accounts.conftest import make_account
from tests.features.spot import regression_scenario as scenario
from tests.features.spot.conftest import T0, D
from trader.features.accounts.ports import AccountsPortImpl
from trader.features.accounts.settings import Account
from trader.features.spot.fake import run_fake_analyze
from trader.kernel.config import Secrets
from trader.kernel.lane.types import LaneContext
from trader.kernel.notify import NullNotifier
from trader.kernel.session import run_store
from trader.kernel.session.run_store import list_checkpoints, run_dir
from trader.kernel.session.runner import SessionSpec

SNAPSHOT = Path(__file__).parents[2] / "fixtures" / "stock_regression_v1.json"


def test_stock_paths_unchanged(project, conn, settings):
    got = json.loads(
        json.dumps(
            scenario.run(settings, conn, scenario.RegressionPort()),
            ensure_ascii=False,
            sort_keys=True,
            default=str,
        )
    )
    if os.environ.get("TRADER_UPDATE_GOLDEN") == "1":
        SNAPSHOT.write_text(
            json.dumps(got, ensure_ascii=False, sort_keys=True, indent=4, default=str) + "\n",
            encoding="utf-8",
        )
        return
    assert got == json.loads(SNAPSHOT.read_text(encoding="utf-8"))


def test_stock_orders_tagged_stock_and_accounts_default(project, conn, settings):
    scenario.run(settings, conn, scenario.RegressionPort())
    assets = {r[0] for r in conn.execute("SELECT asset FROM orders")}
    assert assets == {"stock"}
    acct = Account(id="x", env="paper", cano="1", markets=["KR"], mode="auto")
    assert acct.asset == "stock"


EXPECTED_CHECKPOINTS = sorted(
    [
        "00_inputs",
        "10_analyst_fundamental",
        "10_analyst_news_macro",
        "10_analyst_sentiment",
        "10_analyst_technical",
        "20_debate_r0_bear",
        "20_debate_r0_bull",
        "20_debate_r1_bear",
        "20_debate_r1_bull",
        "25_research_summary",
        "30_decision",
        "40_risk_check_paper-main",
        "40_risk_check_real-main",
    ]
)


def test_fake_analyze_writes_expected_checkpoint_set(project, conn, settings, monkeypatch):
    """debate_rounds=1, 계좌 paper-main·real-main 의 FAKE analyze 가 쓰는 체크포인트 13개를 고정."""
    monkeypatch.setenv("FAKE_CLAUDE", "1")
    settings.debate_rounds = 1
    acc = AccountsPortImpl(
        [
            make_account("paper-main", "paper", "auto", ["KR", "US"]),
            make_account("real-main", "real", "off", ["KR"]),
        ],
        Secrets(),
    )
    d = run_dir(D, "KR", "005930")
    run_store.write_checkpoint(
        d,
        "00_inputs",
        {
            "meta": {
                "ticker": "005930",
                "market": "KR",
                "name": "삼성전자",
                "asof": T0.isoformat(),
            },
            "technical": {"quote": {"data": {"price": "71500"}, "received_at": "", "error": None}},
            "fundamental": {},
            "news_macro": {},
            "sentiment": {},
            "errors": [],
        },
    )
    lane_ctx = LaneContext(
        settings=settings,
        conn=conn,
        state=project / "state",
        now=lambda: T0,
        dry_run=True,
        notifier=NullNotifier(),
        ports={"accounts": acc},
    )
    spec = SessionSpec(
        market="KR",
        date=D,
        tickers=[{"ticker": "005930", "exchange": None}],
        dry_run=True,
    )
    run_fake_analyze(lane_ctx, spec)
    assert sorted(list_checkpoints(d)) == EXPECTED_CHECKPOINTS
