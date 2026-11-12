"""시장 순위 원본 보관소(`market_activity.db`): 원장 밖에 쌓이고, 옛 원장의 행은 마이그레이션이
그대로 옮긴다(영구 보관·append-only 유지)."""

import json
import sqlite3
from datetime import UTC, datetime, timedelta

import pytest

from trader.features.quotes import activity_store
from trader.kernel.ledger import control, db
from trader.kernel.registry import discover
from trader.kernel.registry.contracts import all_migrations

T0 = datetime(2026, 11, 6, 15, 0, tzinfo=UTC)


def _migs():
    return all_migrations(discover())


def _parts(n):
    return {"NAS:turnover": {"ok": 1, "rows": [{"ticker": f"T{n}"}]}}


def _tables(conn):
    return {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def test_append_goes_to_archive_file_not_ledger(tmp_path):
    ledger = db.connect(tmp_path / "trader.db")
    db.init_schema(ledger, _migs())
    activity_store.append(ledger, "US", received_at=T0, partitions=_parts(1))
    assert "market_activity_batches" not in _tables(ledger)
    arch = sqlite3.connect(tmp_path / "market_activity.db")
    assert arch.execute("SELECT COUNT(*) FROM market_activity_batches").fetchone()[0] == 1
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        arch.execute("DELETE FROM market_activity_batches")
    # 읽기도 원장 연결을 받아 보관소에서 읽는다
    at, part = activity_store.latest(ledger, "US", "NAS:turnover", asof=T0)
    assert part["rows"] == [{"ticker": "T1"}]
    assert len(activity_store.history(ledger, "US", asof=T0)) == 1


def _old_ledger(path, rows):
    """분리 전(v20) 원장: 순위 테이블이 원장 안에 있고 행이 들어 있다."""
    migs = _migs()
    before = [m for m in migs if m.name != "quotes-activity-archive-split"]
    split = next(m for m in migs if m.name == "quotes-activity-archive-split")
    assert split.version == len(migs), "분리 마이그레이션은 이 PR 시점의 마지막 버전"
    conn = db.connect(path)
    db.init_schema(conn, before)
    for i in range(rows):
        stamp = (T0 + timedelta(seconds=30 * i)).isoformat()
        cur = conn.execute(
            "INSERT INTO market_activity_batches(market,received_at,payload_json) VALUES (?,?,?)",
            ("US", stamp, json.dumps(_parts(i))),
        )
        conn.execute(
            "INSERT INTO market_activity_partitions(batch_id,market,label,ok,received_at) "
            "VALUES (?,?,?,?,?)",
            (cur.lastrowid, "US", "NAS:turnover", 1, stamp),
        )
    conn.commit()
    return conn


def test_migration_moves_existing_rows_and_drops_ledger_tables(tmp_path):
    conn = _old_ledger(tmp_path / "trader.db", rows=250)
    db.init_schema(conn, _migs())
    assert not {"market_activity_batches", "market_activity_partitions"} & _tables(conn)
    arch = sqlite3.connect(tmp_path / "market_activity.db")
    stats = arch.execute("SELECT COUNT(*), MIN(id), MAX(id) FROM market_activity_batches")
    assert stats.fetchone() == (250, 1, 250)
    assert arch.execute("SELECT COUNT(*) FROM market_activity_partitions").fetchone()[0] == 250
    asof = T0 + timedelta(seconds=30 * 249)
    _, part = activity_store.latest(conn, "US", "NAS:turnover", asof=asof)
    assert part["rows"] == [{"ticker": "T249"}]
    assert control.get_flag(conn, "ledger_vacuum_pending")  # 빈 공간 회수는 데몬 기동 때
    # 옮긴 뒤 새 배치는 이어지는 id 로 쌓인다
    later = asof + timedelta(seconds=30)
    activity_store.append(conn, "US", received_at=later, partitions=_parts(9))
    assert arch.execute("SELECT MAX(id) FROM market_activity_batches").fetchone()[0] == 251


def test_migration_resumes_after_partial_copy(tmp_path):
    """복사 도중 끊겼다 다시 돌아도(보관소에 일부 행이 이미 있음) 중복 없이 끝난다."""
    conn = _old_ledger(tmp_path / "trader.db", rows=30)
    arch = db.connect(tmp_path / "market_activity.db")
    activity_store.ensure_schema(arch)
    arch.execute(
        "INSERT INTO market_activity_batches(id,market,received_at,payload_json) "
        "SELECT 1,'US',?, '{}'",
        (T0.isoformat(),),
    )
    arch.commit()
    arch.close()
    db.init_schema(conn, _migs())
    arch = sqlite3.connect(tmp_path / "market_activity.db")
    assert arch.execute("SELECT COUNT(*) FROM market_activity_batches").fetchone()[0] == 30
    assert arch.execute("SELECT COUNT(*) FROM market_activity_partitions").fetchone()[0] == 30


def test_fresh_ledger_has_no_activity_tables_and_no_vacuum_flag(tmp_path):
    conn = db.connect(tmp_path / "trader.db")
    db.init_schema(conn, _migs())
    assert "market_activity_batches" not in _tables(conn)
    assert not control.get_flag(conn, "ledger_vacuum_pending")


def test_bootstrap_hook_reclaims_ledger_space_once(tmp_path):
    """이전으로 비운 원장은 데몬 기동 훅이 VACUUM 으로 줄이고 예약 플래그를 지운다."""
    from types import SimpleNamespace

    from trader.features.quotes import jobs

    path = tmp_path / "trader.db"
    conn = _old_ledger(path, rows=400)
    db.init_schema(conn, _migs())
    conn.close()
    before = path.stat().st_size
    deps = SimpleNamespace(conn_factory=lambda: db.connect(path))
    assert jobs.reclaim_ledger(deps=deps) == {"vacuumed": True}
    assert path.stat().st_size < before
    check = db.connect(path)
    assert not control.get_flag(check, "ledger_vacuum_pending")
    assert jobs.reclaim_ledger(deps=deps) == {"vacuumed": False}


def test_history_parses_each_batch_once_across_calls(tmp_path, monkeypatch):
    """30초마다 최근 14분치(수 MB JSON)를 통째로 다시 파싱하지 않는다 — 이미 읽은 배치는
    메모리에 두고 새 배치만 읽는다. 창 밖으로 나간 배치는 버린다."""
    ledger = db.connect(tmp_path / "trader.db")
    db.init_schema(ledger, _migs())
    for i in range(3):
        at = T0 + timedelta(seconds=30 * i)
        activity_store.append(ledger, "US", received_at=at, partitions=_parts(i))
    parsed = []
    real = json.loads
    monkeypatch.setattr(
        activity_store.json, "loads", lambda s, *a, **k: (parsed.append(1), real(s, *a, **k))[1]
    )
    asof = T0 + timedelta(seconds=60)
    first = activity_store.history(ledger, "US", asof=asof)
    assert len(first) == 3 and len(parsed) == 3
    assert activity_store.history(ledger, "US", asof=asof) == first and len(parsed) == 3
    activity_store.append(ledger, "US", received_at=asof + timedelta(seconds=30), partitions={})
    assert len(activity_store.history(ledger, "US", asof=asof + timedelta(seconds=30))) == 4
    assert len(parsed) == 4
    # 과거 시점 조회는 그 시점 이후 배치를 섞지 않는다(룩어헤드 금지)
    assert len(activity_store.history(ledger, "US", asof=T0)) == 1
    # 창(14분)을 벗어나면 결과에서도 빠진다
    late = T0 + timedelta(minutes=14, seconds=45)
    assert [at for at, _ in activity_store.history(ledger, "US", asof=late)] == [
        T0 + timedelta(seconds=60), T0 + timedelta(seconds=90),
    ]  # fmt: skip
