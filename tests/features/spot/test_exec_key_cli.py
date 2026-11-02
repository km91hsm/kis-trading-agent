"""`trader spot exec-key list|release`: 보낸 뒤 기록 전에 끊겨 영구 선점된 실행 키를 사람이
보고 푼다.

옛 `trader control exec-key` (tests/test_cli_control.py) 에서 옮겼다. 해제는 사유 10자 이상 필수이고
control `exec_key_released:<key>` 에 {note, at} 를 남긴다."""

from typer.testing import CliRunner

from trader.kernel.cli.main import app
from trader.kernel.execution.idem import ExecKeyStore
from trader.kernel.ledger import control, db
from trader.kernel.registry import discover
from trader.kernel.registry.contracts import all_migrations

KEY = "7|paper-main|BUY|50_order"


def _seed() -> None:
    conn = db.connect()
    try:
        db.init_schema(conn, all_migrations(discover()))
        store = ExecKeyStore(conn)
        assert store.claim(KEY, now="2026-10-09T01:00:00+00:00")
        store.mark_sent(KEY, sent_at="2026-10-09T01:00:05+00:00")
    finally:
        conn.close()


def test_exec_key_list_shows_columns(project):
    _seed()
    r = CliRunner().invoke(app, ["spot", "exec-key", "list"])
    assert r.exit_code == 0, r.output
    assert KEY in r.stdout and "2026-10-09T01:00:05+00:00" in r.stdout
    for col in ("key", "created_at", "order_id", "approval_id", "sent_at"):
        assert col in r.stdout


def test_exec_key_release_requires_note_and_records_flag(project):
    _seed()
    runner = CliRunner()
    r = runner.invoke(app, ["spot", "exec-key", "release", KEY])
    assert r.exit_code == 1 and "사유는 10자 이상" in r.output  # 사유 없이는 풀지 않는다
    r = runner.invoke(app, ["spot", "exec-key", "release", KEY, "--note", "짧은 메모"])
    assert r.exit_code == 1 and "사유는 10자 이상" in r.output
    conn = db.connect()
    try:
        assert [k["key"] for k in ExecKeyStore(conn).list_all()] == [KEY]
    finally:
        conn.close()
    r = runner.invoke(app, ["spot", "exec-key", "release", KEY, "--note", "KIS 주문 없음 확인함"])
    assert r.exit_code == 0, r.output
    conn = db.connect()
    try:
        assert ExecKeyStore(conn).list_all() == []
        flag = control.get_flag(conn, f"exec_key_released:{KEY}")
        assert flag["note"] == "KIS 주문 없음 확인함" and flag["at"]
    finally:
        conn.close()
    r = runner.invoke(
        app, ["spot", "exec-key", "release", "none", "--note", "메모 열 자 이상입니다"]
    )
    assert r.exit_code == 1


def test_exec_key_release_records_flag_before_releasing(project, monkeypatch):
    """해제 기록을 먼저 남긴다 — 해제 뒤 어떤 실패가 나도 감사 기록은 남아 있어야 한다."""
    _seed()
    seen: list[bool] = []

    def release_then_fail(self, key):
        seen.append(control.get_flag(self.conn, f"exec_key_released:{key}") is not None)
        raise RuntimeError("해제 실패")

    monkeypatch.setattr(ExecKeyStore, "release", release_then_fail)
    args = ["spot", "exec-key", "release", KEY, "--note", "KIS 주문 없음 확인함"]
    r = CliRunner().invoke(app, args)
    assert isinstance(r.exception, RuntimeError) and seen == [True]
