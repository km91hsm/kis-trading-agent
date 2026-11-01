from pathlib import Path

from trader.kernel.registry.contracts import import_violations

FIXTURES = Path(__file__).parents[2] / "fixtures"
SRC = Path(__file__).parents[3] / "src"


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_import_boundary_flags_internal_import():
    v = import_violations(FIXTURES / "features_bad", package="tests.fixtures.features_bad")
    assert any("gamma" in m and "alpha.internal" in m for m in v)


def test_api_import_is_allowed():
    v = import_violations(FIXTURES / "features_ok", package="tests.fixtures.features_ok")
    assert v == []


def test_from_import_api_allowed_other_name_flagged(tmp_path):
    _write(tmp_path / "a" / "__init__.py", "")
    _write(tmp_path / "b" / "__init__.py", "")
    _write(tmp_path / "b" / "ok.py", "from pkg.a import api\n")
    _write(tmp_path / "b" / "bad.py", "from pkg.a import internal\n")
    v = import_violations(tmp_path, package="pkg")
    assert len(v) == 1 and "b.bad" in v[0]


def test_relative_import_resolved(tmp_path):
    _write(tmp_path / "a" / "__init__.py", "")
    _write(tmp_path / "b" / "x.py", "from ..a.core import f\n")
    v = import_violations(tmp_path, package="pkg")
    assert any("a.core" in m for m in v)


def test_lane_may_import_no_feature(tmp_path):
    _write(tmp_path / "spot" / "x.py", "from pkg.scan.api import f\n")
    _write(tmp_path / "scan" / "x.py", "from pkg.spot.api import f\n")
    v = import_violations(tmp_path, package="pkg")
    assert any("spot" in m and "레인" in m for m in v)


def test_own_feature_and_kernel_imports_allowed(tmp_path):
    _write(
        tmp_path / "features" / "a" / "x.py", "from pkg.features.a.helpers import f\nimport json\n"
    )
    _write(tmp_path / "features" / "a" / "y.py", "from pkg.kernel.ledger import connect\n")
    assert import_violations(tmp_path, package="pkg") == []


def test_kernel_may_not_import_features(tmp_path):
    _write(tmp_path / "features" / "a" / "__init__.py", "")
    _write(tmp_path / "kernel" / "web" / "x.py", "from pkg.features.a.api import f\n")
    _write(tmp_path / "kernel" / "registry" / "discover.py", "from pkg.features import a\n")
    v = import_violations(tmp_path, package="pkg")
    assert len(v) == 1 and "kernel" in v[0] and "web" in v[0]


def test_real_tree_has_no_violations():
    assert import_violations(SRC / "trader") == []
