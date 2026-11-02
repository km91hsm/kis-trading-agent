import json
from datetime import UTC, datetime

from tests.features.ingest.helpers import FakeMarket
from trader.features.ingest.api import SourcesConfig
from trader.features.ingest.ports import InputsPortImpl
from trader.kernel.config import Secrets
from trader.kernel.ledger import db
from trader.kernel.platform import paths


def test_collect_writes_00_inputs_and_returns_payload(project):
    cfg = SourcesConfig.model_validate(
        {
            "edgar": {"user_agent": "trader test@example.com"},
            "news": {"enabled": False},
            "macro": {"enabled": False},
            "community": {"enabled": False},
        }
    )
    port = InputsPortImpl(Secrets(), cfg)
    market = FakeMarket()
    out = port.collect(
        ticker="005930",
        market="KR",
        exchange=None,
        asof=datetime.now(UTC),
        force=False,
        conn_factory=db.connect,
        market_client=market,
    )
    files = list(paths.state_dir().rglob("00_inputs.json"))
    assert len(files) == 1
    assert json.loads(files[0].read_text(encoding="utf-8")) == out
    assert out["technical"]["quote"]["data"]["price"] == "71500"
    assert "get_daily_kr" in market.calls
    # 있으면 읽기만(시세 재호출 없음)
    again = port.collect(
        ticker="005930",
        market="KR",
        exchange=None,
        asof=datetime.now(UTC),
        force=False,
        conn_factory=db.connect,
        market_client=market,
    )
    assert again == out and market.calls.count("get_daily_kr") == 1
