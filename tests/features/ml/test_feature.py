"""ml 기능 선언."""

from trader.features.ml.feature import FEATURE
from trader.kernel.cli.boot import build_registry
from trader.kernel.config.loader import load
from trader.kernel.platform.bundle import templates_dir
from trader.kernel.registry import discover


def test_feature_declaration():
    assert FEATURE.id == "ml" and FEATURE.tier == "service"
    assert [m.version for m in FEATURE.migrations] == [12]
    assert {(s.file, s.key) for s in FEATURE.sections} == {
        ("ml", "label"),
        ("ml", "train"),
        ("ml", "models"),
        ("schedule", "ml_train"),
        ("schedule", "universe_members"),
        ("schedule", "features_snapshot"),
    }


def test_registered_and_template_parity():
    reg = build_registry(discover())
    assert set(reg.keys("ml")) == {"label", "train", "models"}
    load("ml", reg, path=templates_dir() / "config" / "ml.yaml")


def test_universe_members_table(conn):
    cols = [r[1] for r in conn.execute("PRAGMA table_info(universe_members)")]
    assert cols == ["market", "ticker", "first_seen", "last_seen", "source"]


def test_features_sse_key_changes_on_snapshot(tmp_path):
    import os
    from types import SimpleNamespace

    import pandas as pd

    from trader.features.ml import registry

    (key,) = [k for k in FEATURE.sse_keys if k.key == "features"]
    deps = SimpleNamespace(state=tmp_path)
    fp0 = key.probe(deps)
    assert key.probe(deps) == fp0 and not (tmp_path / "features").exists()  # 디렉터리를 만들지 않음
    fdir = tmp_path / "features"
    fdir.mkdir()
    pd.DataFrame({"ticker": ["A"]}).to_parquet(fdir / "2026-11-03.parquet")
    os.utime(fdir, (1_000, 1_000))
    fp1 = key.probe(deps)
    assert fp1 != fp0
    pd.DataFrame({"ticker": ["A"]}).to_parquet(fdir / "2026-11-04.parquet")
    os.utime(fdir, (1_000, 1_000))  # mtime 이 같아도 최신 asof 로 바뀐다
    fp2 = key.probe(deps)
    assert fp2 != fp1
    v = registry.save("quant", {"m": 1}, {}, state=tmp_path)
    fp3 = key.probe(deps)
    assert fp3 != fp2
    registry.promote("quant", v, state=tmp_path)
    assert key.probe(deps) != fp3
