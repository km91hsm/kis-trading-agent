"""`trader migrate-home`: 옛 휴대용 폴더 → 현재 데이터 루트 복사(SQLite backup·제외·거부 조건)."""

import json
import os
import sqlite3
import sys
from pathlib import Path

import pytest
from typer.testing import CliRunner

from trader.kernel.cli import migrate_cmd
from trader.kernel.cli.main import app

SOURCES_YAML = """# 수집 소스
news:
  enabled: true
saveticker:
  # 수집 폴더(상대 경로는 프로젝트 루트 기준)
  dir: {dir}  # 확장 수집 폴더
  collect: true
other:
  dir: /keep/this
"""


def _wal_db(path: Path, rows: int) -> sqlite3.Connection:
    """WAL 모드 DB 를 만들고 체크포인트되지 않은 쓰기를 남긴 채 연결을 돌려준다."""
    path.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(str(path))
    c.execute("PRAGMA journal_mode=WAL")
    c.execute("PRAGMA wal_autocheckpoint=0")
    c.execute("CREATE TABLE t(x INTEGER)")
    c.executemany("INSERT INTO t VALUES (?)", [(i,) for i in range(rows)])
    c.commit()
    return c


@pytest.fixture
def old(tmp_path):
    """옛 휴대용 폴더: config/secrets/state/docs + WAL 원장 + 런타임 잠금 파일."""
    root = tmp_path / "old-trader"
    (root / "config").mkdir(parents=True)
    (root / "config" / "settings.yaml").write_text("web:\n  port: 8787\n", encoding="utf-8")
    (root / "config" / "sources.yaml").write_text(
        SOURCES_YAML.format(dir="/Users/me/saveticker-data"), encoding="utf-8"
    )
    (root / "secrets").mkdir()
    (root / "secrets" / ".env").write_text("WEB_TOKEN=s3cr3t-value\n", encoding="utf-8")
    (root / "docs").mkdir()
    (root / "docs" / "guide.md").write_text("# g\n", encoding="utf-8")
    state = root / "state"
    (state / "logs").mkdir(parents=True)
    (state / "logs" / "daemon.log").write_text("로그\n", encoding="utf-8")
    (state / "daemon.lock").write_text(json.dumps({"pid": 2**31 - 5}), encoding="utf-8")
    (state / "daemon.lock.os").write_text("", encoding="utf-8")
    (state / "daemon_heartbeat.json").write_text("{}", encoding="utf-8")
    conn = _wal_db(state / "trader.db", 50)
    assert (state / "trader.db-wal").exists()  # 쓰기가 아직 WAL 에만 있다
    yield root
    conn.close()


@pytest.fixture
def saveticker(tmp_path):
    root = tmp_path / "st"
    (root / "data" / "2026-11-01").mkdir(parents=True)
    (root / "data" / "2026-11-01" / "items.jsonl").write_text('{"id": 1}\n', encoding="utf-8")
    (root / "runtime").mkdir()
    bridge = root / "runtime" / "bridge-config.json"
    bridge.write_text('{"backend": "extension"}', encoding="utf-8")
    (root / "runtime" / "service.lock").write_text("{}", encoding="utf-8")
    (root / "runtime" / "other.json").write_text("{}", encoding="utf-8")
    return root


@pytest.fixture
def dest(tmp_path, monkeypatch):
    root = tmp_path / "data-root"
    root.mkdir()
    monkeypatch.setenv("TRADER_HOME", str(root))
    monkeypatch.chdir(tmp_path)
    return root.resolve()


def _invoke(*args):
    r = CliRunner().invoke(app, ["migrate-home", *args])
    lines = [ln for ln in r.output.splitlines() if ln.strip()]
    return r, json.loads(lines[-1])


def test_migrate_copies_dirs_with_sqlite_backup_and_skips_runtime_files(old, dest):
    r, out = _invoke("--from", str(old))
    assert r.exit_code == 0, r.output
    assert out["ok"] is True and out["dry_run"] is False
    assert out["to"] == str(dest) and out["from"] == str(old.resolve())
    assert out["databases"] == ["state/trader.db"]
    assert set(out["copied"]) == {"config", "secrets", "state", "docs"}
    assert out["files"] == sum(v["files"] for v in out["copied"].values())
    assert {"state/daemon.lock", "state/daemon.lock.os", "state/daemon_heartbeat.json",
            "state/trader.db-wal", "state/trader.db-shm"} <= set(out["skipped"])  # fmt: skip
    # WAL 에만 있던 쓰기까지 포함된 일관된 사본, 부속 파일은 만들지 않는다
    c = sqlite3.connect(str(dest / "state" / "trader.db"))
    try:
        assert c.execute("SELECT COUNT(*) FROM t").fetchone()[0] == 50
    finally:
        c.close()
    assert not (dest / "state" / "trader.db-wal").exists()
    assert not (dest / "state" / "daemon.lock").exists()
    assert not (dest / "state" / "daemon_heartbeat.json").exists()
    assert (dest / "state" / "logs" / "daemon.log").read_text(encoding="utf-8") == "로그\n"
    assert (dest / "docs" / "guide.md").is_file()
    # 원본은 그대로
    assert (old / "state" / "trader.db").is_file() and (old / "secrets" / ".env").is_file()
    # 시크릿은 복사만 하고 내용은 출력하지 않는다
    assert "s3cr3t-value" not in r.output
    assert (dest / "secrets" / ".env").read_text(encoding="utf-8") == "WEB_TOKEN=s3cr3t-value\n"
    # saveticker 옵션이 없으면 sources.yaml 은 원본 그대로
    assert "/Users/me/saveticker-data" in (dest / "config" / "sources.yaml").read_text(
        encoding="utf-8"
    )


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX 권한 비트")
def test_migrate_secrets_permissions(old, dest):
    os.chmod(old / "secrets" / ".env", 0o644)
    r, _ = _invoke("--from", str(old))
    assert r.exit_code == 0, r.output
    assert (dest / "secrets").stat().st_mode & 0o777 == 0o700
    assert (dest / "secrets" / ".env").stat().st_mode & 0o777 == 0o600


def test_migrate_dry_run_writes_nothing(old, dest, saveticker):
    r, out = _invoke("--from", str(old), "--saveticker-from", str(saveticker), "--dry-run")
    assert r.exit_code == 0, r.output
    assert out["dry_run"] is True and out["files"] > 0 and out["bytes"] > 0
    assert out["saveticker_dir_reset"] == "/Users/me/saveticker-data"
    assert list(dest.iterdir()) == []


def test_migrate_saveticker_from_copies_data_and_resets_dir(old, dest, saveticker):
    r, out = _invoke("--from", str(old), "--saveticker-from", str(saveticker))
    assert r.exit_code == 0, r.output
    st = dest / "state" / "external" / "saveticker"
    assert (st / "data" / "2026-11-01" / "items.jsonl").is_file()
    assert json.loads((st / "runtime" / "bridge-config.json").read_text(encoding="utf-8")) == {
        "backend": "extension"
    }
    assert not (st / "runtime" / "service.lock").exists()
    assert not (st / "runtime" / "other.json").exists()  # runtime 은 bridge-config 만
    assert out["copied"]["state/external/saveticker"]["files"] == 2
    assert out["saveticker_dir_reset"] == "/Users/me/saveticker-data"
    text = (dest / "config" / "sources.yaml").read_text(encoding="utf-8")
    assert text == SOURCES_YAML.format(dir="state/external/saveticker")  # 주석·다른 절 보존


def test_reset_saveticker_dir_keeps_relative(tmp_path):
    p = tmp_path / "sources.yaml"
    p.write_text(SOURCES_YAML.format(dir="state/external/saveticker"), encoding="utf-8")
    assert migrate_cmd.reset_saveticker_dir(p, dry_run=False) is None
    p.write_text(SOURCES_YAML.format(dir='"C:\\\\Users\\\\me\\\\st"'), encoding="utf-8")
    assert migrate_cmd.reset_saveticker_dir(p, dry_run=False) == "C:\\\\Users\\\\me\\\\st"
    assert "dir: state/external/saveticker  # 확장 수집 폴더" in p.read_text(encoding="utf-8")


def test_migrate_refuses_when_target_has_ledger(old, dest):
    (dest / "state").mkdir()
    (dest / "state" / "trader.db").write_bytes(b"")
    r, out = _invoke("--from", str(old))
    assert r.exit_code == 1
    assert out["ok"] is False and "이미 원장" in out["error"]


@pytest.mark.parametrize("where", ["old", "dest"])
def test_migrate_refuses_when_daemon_lock_held(old, dest, where):
    root = old if where == "old" else dest
    (root / "state").mkdir(exist_ok=True)
    (root / "state" / "daemon.lock").write_text(json.dumps({"pid": os.getpid()}), encoding="utf-8")
    r, out = _invoke("--from", str(old))
    assert r.exit_code == 1
    assert "데몬을 먼저 종료" in out["error"]
    assert not (dest / "config").exists()


def test_migrate_refuses_when_global_lock_owned_by_source(old, dest):
    from trader.kernel.daemon.lock import global_lock

    g = global_lock(old)
    assert g.acquire() is True
    try:
        r, out = _invoke("--from", str(old))
    finally:
        g.release()
    assert r.exit_code == 1 and "데몬을 먼저 종료" in out["error"]


def test_migrate_refuses_same_or_missing_source(dest, tmp_path):
    r, out = _invoke("--from", str(dest))
    assert r.exit_code == 1 and "같습니다" in out["error"]
    r, out = _invoke("--from", str(tmp_path / "nope"))
    assert r.exit_code == 1 and "없습니다" in out["error"]
