"""MlPort.close_table·snapshot(extra_targets) (M8 Ruling A6·A7)."""

from datetime import date
from types import SimpleNamespace

import pandas as pd

from trader.features.ml import features, ports
from trader.features.ml.ports import MlPortImpl

D0, D1, D2 = date(2026, 1, 5), date(2026, 1, 6), date(2026, 1, 7)


def test_port_close_table_mapping_shape(project, settings):
    rows = [(d, t, c) for d, c in ((D0, 100.0), (D1, 101.0), (D2, 102.0)) for t in "AB"]
    rows = [(d, t, c + (1 if t == "B" else 0)) for d, t, c in rows]
    df = pd.DataFrame(
        {
            "date": [r[0] for r in rows], "ticker": [r[1] for r in rows],
            "market": ["KR"] * 6, "close": [r[2] for r in rows], "volume": [1000] * 6,
        }
    )  # fmt: skip
    features.write_rows(df, derive=False)
    port = MlPortImpl(settings, None)
    out = port.close_table("KR", D0, D2)
    assert out["A"] == {D0: 100.0, D1: 101.0, D2: 102.0}
    assert out["B"][D0] == 101.0
    assert port.close_table("US", D0, D2) == {}


def test_port_snapshot_passes_extra_targets(project, settings, monkeypatch):
    seen = {}
    monkeypatch.setattr(features, "snapshot", lambda **kw: seen.update(kw) or 3)
    monkeypatch.setattr(ports.ingest_api, "build_dart", lambda s: None)
    monkeypatch.setattr(ports.ingest_api, "build_edgar", lambda *a: None)
    monkeypatch.setattr(ports.ingest_api, "load_sources", lambda r: None)
    monkeypatch.setattr(ports.ingest_api, "load_universe", lambda: None)
    acc = SimpleNamespace(market_client=lambda: object())
    port = MlPortImpl(settings, None, {"accounts": acc})
    n = port.snapshot(["KR"], extra_targets=[("247540", "KR", None)])
    assert n == 3
    assert seen["extra_targets"] == [("247540", "KR", None)]
