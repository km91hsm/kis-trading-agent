"""ml 포트 (`ports["ml"]`)."""

from datetime import date
from types import SimpleNamespace

import pandas as pd

from trader.features.ingest.api import SectorMap
from trader.features.ml import features
from trader.features.ml.ports import PORT, MlPortImpl, build_port

SECTORS = SectorMap(sectors=["반도체", "기타"], kr={"A": "반도체", "B": "반도체", "C": "반도체"})
DAY = date(2026, 1, 5)


def _port(project, settings):
    (project / "config").mkdir(exist_ok=True)
    (project / "config" / "universe.yaml").write_text("kr: ['A', 'B', 'C']\n", encoding="utf-8")
    return MlPortImpl(settings, None)


def _write(tickers_roe: dict[str, float]):
    n = len(tickers_roe)
    df = pd.DataFrame(
        {
            "date": [DAY] * n, "ticker": list(tickers_roe), "market": ["KR"] * n,
            "close": [100.0] * n, "volume": [1000] * n, "roe": list(tickers_roe.values()),
            "op_margin": [0.1] * n, "debt_ratio": [1.0] * n,
        }
    )  # fmt: skip
    features.write_rows(df, derive=False)


def test_port_declaration_and_build(settings):
    assert PORT.name == "ml"
    port = build_port(SimpleNamespace(settings=settings, registry=None, ports={}, secrets=None))
    assert isinstance(port, MlPortImpl)


def test_port_latest_model_none_without_promoted(project, settings):
    assert _port(project, settings).latest_model("KR") is None


def test_peer_medians_none_under_three_peers_and_values_with_three(project, settings):
    port = _port(project, settings)
    kw = dict(market="KR", ticker="A", asof_date=DAY, sectors=SECTORS)
    assert port.peer_medians(**{**kw, "ticker": "Z"}) is None  # 섹터 모름
    _write({"A": 0.1, "B": 0.2})
    assert port.peer_medians(**kw) is None  # 구성원 2 < 3
    _write({"A": 0.1, "B": 0.2, "C": 0.4})
    out = port.peer_medians(**kw)
    assert out["sector"] == "반도체" and out["n"] == 3
    assert out["median"]["ROE"] == 0.2 and out["mine"]["ROE"] == 0.1
    assert abs(out["diff"]["ROE"] - (-0.1)) < 1e-9
    assert out["dates"] == {"min": "2026-01-05", "max": "2026-01-05"}


def test_quant_enabled_false_on_broken_yaml(project, settings):
    (project / "config").mkdir(exist_ok=True)
    (project / "config" / "ml.yaml").write_text("models: [\n", encoding="utf-8")
    assert MlPortImpl(settings, None).quant_enabled() is False


def test_snapshot_requires_market_client(project, settings):
    import pytest

    port = MlPortImpl(settings, None, {"accounts": SimpleNamespace(market_client=lambda: None)})
    with pytest.raises(RuntimeError, match="시세 클라이언트"):
        port.snapshot(["KR"])
