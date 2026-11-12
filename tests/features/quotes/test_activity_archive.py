"""시장 순위 원본 보관소(`market_activity.db`): 원장 밖에 쌓이고, 옛 원장의 행은 마이그레이션이
그대로 옮긴다(영구 보관·append-only 유지)."""

import json
import sqlite3
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from trader.features.quotes import activity_move, activity_store, jobs
from trader.kernel.ledger import db
from trader.kernel.registry import discover
from trader.kernel.registry.contracts import all_migrations

T0 = datetime(2026, 11, 6, 15, 0, tzinfo=UTC)


def _migs():
    return all_migrations(discover())


def _parts(n):
    return {"NAS:turnover": {"ok": 1, "rows": [{"ticker": f"T{n}"}]}}


def _tables(conn):
    return {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def _count(conn, table="market_activity_batches"):
    return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]  # noqa: S608


def test_append_goes_to_archive_file_not_ledger(tmp_path):
    ledger = db.connect(tmp_path / "trader.db")
    db.init_schema(ledger, _migs())
    activity_store.append(ledger, "US", received_at=T0, partitions=_parts(1))
    assert _count(ledger) == 0  # 원장의 옛 테이블에는 더 쓰지 않는다
    arch = sqlite3.connect(tmp_path / "market_activity.db")
    assert _count(arch) == 1
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        arch.execute("DELETE FROM market_activity_batches")
    # 읽기도 원장 연결을 받아 보관소에서 읽는다
    at, part = activity_store.latest(ledger, "US", "NAS:turnover", asof=T0)
    assert part["rows"] == [{"ticker": "T1"}]
    assert len(activity_store.history(ledger, "US", asof=T0)) == 1


def _old_ledger(path, rows):
    """분리 전 원장: 순위 관측이 원장 안의 테이블에 들어 있다."""
    conn = db.connect(path)
    db.init_schema(conn, _migs())
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


def _deps(path, sent=None):
    box = sent if sent is not None else []
    notifier = SimpleNamespace(send=lambda text, **kw: box.append(text) or True)
    return SimpleNamespace(conn_factory=lambda: db.connect(path), notifier=notifier)


def test_schema_init_alone_never_moves_or_drops_anything(tmp_path):
    """원장을 여는 것만으로는(watchdog·세션 도구·CLI 가 먼저 뜨는 경우) 대용량 복사도 테이블
    삭제도 일어나지 않는다 — 살아 있는 옛 버전 데몬 밑에서 테이블이 사라지면 안 된다."""
    conn = _old_ledger(tmp_path / "trader.db", rows=5)
    db.init_schema(conn, _migs())
    assert _count(conn) == 5 and not (tmp_path / "market_activity.db").exists()


def test_bootstrap_hook_moves_rows_drops_ledger_tables_and_reclaims_space(tmp_path):
    path = tmp_path / "trader.db"
    conn = _old_ledger(path, rows=400)
    conn.close()
    before = path.stat().st_size
    out = jobs.reclaim_ledger(deps=_deps(path))
    assert out == {"moved": 400, "dropped": True, "vacuumed": True}
    assert path.stat().st_size < before
    conn = db.connect(path)
    assert not {"market_activity_batches", "market_activity_partitions"} & _tables(conn)
    arch = sqlite3.connect(tmp_path / "market_activity.db")
    stats = arch.execute("SELECT COUNT(*), MIN(id), MAX(id) FROM market_activity_batches")
    assert stats.fetchone() == (400, 1, 400)
    assert _count(arch, "market_activity_partitions") == 400
    asof = T0 + timedelta(seconds=30 * 399)
    _, part = activity_store.latest(conn, "US", "NAS:turnover", asof=asof)
    assert part["rows"] == [{"ticker": "T399"}]
    # 옮긴 뒤 새 배치는 이어지는 id 로 쌓이고, 다시 불러도 할 일이 없다
    later = asof + timedelta(seconds=30)
    activity_store.append(conn, "US", received_at=later, partitions=_parts(9))
    assert arch.execute("SELECT MAX(id) FROM market_activity_batches").fetchone()[0] == 401
    again = jobs.reclaim_ledger(deps=_deps(path))
    assert again == {"moved": 0, "dropped": False, "vacuumed": False}


def test_move_resumes_after_partial_copy(tmp_path):
    """복사 도중 끊겼다 다시 돌아도(보관소에 같은 행 일부가 이미 있음) 중복 없이 끝난다."""
    path = tmp_path / "trader.db"
    conn = _old_ledger(path, rows=30)
    arch = db.connect(tmp_path / "market_activity.db")
    activity_store.ensure_schema(arch)
    arch.execute(
        "INSERT INTO market_activity_batches(id,market,received_at,payload_json) "
        "SELECT id,market,received_at,payload_json FROM (SELECT 1 AS id, 'US' AS market, "
        "? AS received_at, ? AS payload_json)",
        (T0.isoformat(), json.dumps(_parts(0))),
    )
    arch.commit()
    arch.close()
    assert activity_move.move_out(conn) == {"moved": 30, "dropped": True}
    arch = sqlite3.connect(tmp_path / "market_activity.db")
    assert _count(arch) == 30 and _count(arch, "market_activity_partitions") == 30


def test_move_stops_when_archive_holds_different_rows_under_the_same_ids(tmp_path):
    """되돌렸다 다시 올린 경우처럼 보관소에 같은 id 로 **다른 관측**이 있으면 덮지도 버리지도 않고
    멈춘다 — 원장은 그대로 두고 기동은 계속하며 알린다."""
    path = tmp_path / "trader.db"
    conn = _old_ledger(path, rows=3)
    other = T0 + timedelta(days=1)
    activity_store.append(conn, "US", received_at=other, partitions=_parts(77))  # 보관소 id 1
    conn.close()
    sent: list[str] = []
    out = jobs.reclaim_ledger(deps=_deps(path, sent))
    assert out["dropped"] is False and "MoveConflict" in out["error"]
    assert _count(db.connect(path)) == 3  # 원장의 관측은 그대로
    assert len(sent) == 1 and "순위 관측 분리 실패" in sent[0]


def test_fresh_ledger_just_drops_its_empty_tables(tmp_path):
    path = tmp_path / "trader.db"
    db.init_schema(db.connect(path), _migs())
    out = jobs.reclaim_ledger(deps=_deps(path))
    assert out == {"moved": 0, "dropped": True, "vacuumed": False}
    assert "market_activity_batches" not in _tables(db.connect(path))


def test_archive_recreated_after_file_is_removed_at_runtime(tmp_path):
    """실행 중에 보관소 파일을 옮기거나 지워도(디스크 정리) 다음 쓰기가 새 파일을 만든다. 옛
    파일의 배치로 채운 이력 캐시는 버린다(새 파일은 id 를 1부터 다시 쓴다)."""
    ledger = db.connect(tmp_path / "trader.db")
    db.init_schema(ledger, _migs())
    activity_store.append(ledger, "US", received_at=T0, partitions=_parts(1))
    assert activity_store.history(ledger, "US", asof=T0)[0][1] == _parts(1)
    for f in tmp_path.glob("market_activity.db*"):
        f.unlink()
    activity_store.append(ledger, "US", received_at=T0, partitions=_parts(2))
    assert activity_store.history(ledger, "US", asof=T0)[0][1] == _parts(2)


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
