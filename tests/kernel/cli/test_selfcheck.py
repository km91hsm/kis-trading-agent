import importlib
import json
import sys
from pathlib import Path

from typer.testing import CliRunner

from trader.kernel.cli import selfcheck as selfcheck_mod
from trader.kernel.cli.main import app
from trader.kernel.ledger import db
from trader.kernel.registry import discover
from trader.kernel.registry.contracts import all_migrations

KEYS = [
    "ok",
    "python",
    "root",
    "frozen",
    "ledger_version",
    "features",
    "web_dist",
    "modules",
    "failed",
    "optional",
]


def _run() -> tuple[int, dict]:
    r = CliRunner().invoke(app, ["selfcheck"])
    lines = r.stdout.strip().splitlines()
    assert len(lines) == 1, r.output
    return r.exit_code, json.loads(lines[0])


def test_selfcheck_json_has_version_and_features(project):
    # 원장이 이미 있으면 버전만 읽는다(P11: 만들지도 마이그레이션하지도 않음)
    conn = db.connect()
    db.init_schema(conn, all_migrations(discover()))
    conn.close()
    code, data = _run()
    assert code == 0, data
    assert list(data) == KEYS
    assert data["python"] == ".".join(str(x) for x in sys.version_info[:3])
    assert Path(data["root"]) == project.resolve()
    assert data["ledger_version"] == max(m.version for m in all_migrations(discover()))
    assert data["features"] == [
        "accounts",
        "briefing",
        "evaluation",
        "fo",
        "ingest",
        "ml",
        "quotes",
        "regime",
        "saveticker",
        "scalp",
        "scan",
        "spot",
    ]
    assert data["web_dist"] is False
    assert data["frozen"] is False


def test_selfcheck_imports_heavy_stacks(project, monkeypatch):
    code, data = _run()
    assert data["ok"] is True and code == 0, data
    assert data["modules"] == list(selfcheck_mod.HEAVY_MODULES)
    assert data["failed"] == []

    real = importlib.import_module

    def fake(name, *a, **k):
        if name == "pyarrow":
            raise ImportError("pyarrow 없음(테스트)")
        return real(name, *a, **k)

    monkeypatch.setattr(selfcheck_mod.importlib, "import_module", fake)
    code, data = _run()
    assert code == 1
    assert data["ok"] is False
    assert "pyarrow" not in data["modules"]
    assert data["failed"] == [{"module": "pyarrow", "error": "ImportError: pyarrow 없음(테스트)"}]


def test_selfcheck_reports_optional_modules(project, monkeypatch):
    real = importlib.import_module

    def make(missing: set[str]):
        def fake(name, *a, **k):
            if name in missing:
                raise ImportError(name)
            if name in {"lightgbm", "stable_baselines3"}:
                return object()
            return real(name, *a, **k)

        return fake

    monkeypatch.setattr(selfcheck_mod.importlib, "import_module", make(set()))
    code, data = _run()
    assert data["optional"] == {"lightgbm": True, "stable_baselines3": True}
    monkeypatch.setattr(
        selfcheck_mod.importlib, "import_module", make({"lightgbm", "stable_baselines3"})
    )
    code, data = _run()
    # 선택 모듈이 없어도 ok 다
    assert code == 0 and data["ok"] is True
    assert data["optional"] == {"lightgbm": False, "stable_baselines3": False}


def test_selfcheck_does_not_create_db(project):
    code, data = _run()
    assert code == 0, data
    assert data["ledger_version"] is None
    assert not (project / "state" / "trader.db").exists()
