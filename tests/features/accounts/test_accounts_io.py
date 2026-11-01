"""설정 화면의 계좌 추가·삭제(`accounts_io`): 한 줄 추가·한 줄 삭제로 주석을 지키고, 쓰기 전 백업,
쓴 뒤 다시 읽어 검증(실패면 백업으로 복원). 실전은 off, 파생은 03·[KR] 고정, 계좌번호는 자리표시자.
삭제는 살아 있는 주문·승인·최근 보유·열린 파생 포지션이 없을 때만이고 원장 기록은 지우지 않는다."""

import pytest

from trader.features.accounts import accounts_io
from trader.features.accounts.settings import load_accounts
from trader.kernel.config import ConfigError
from trader.kernel.config.secrets import Secrets

YAML = """\
# 계좌 목록 — 주석은 남아야 한다
accounts:
  - {id: paper-main, env: paper, cano: "00000000", markets: [KR, US], mode: auto}
  # 실전은 off 로 시작
  - {id: real-main,  env: real,  cano: "00000000", acnt_prdt_cd: "01", markets: [KR], mode: "off"}
"""


@pytest.fixture
def yml(tmp_path):
    p = tmp_path / "accounts.yaml"
    p.write_text(YAML, encoding="utf-8")
    return p


@pytest.fixture
def db(conn):
    """테스트 전용 최소 스키마 — 주문·승인·잔고·파생 테이블은 M4 이후 실제 스키마로 바꾼다."""
    conn.executescript(
        """
        CREATE TABLE orders(account_id TEXT, ticker TEXT, market TEXT, side TEXT, qty INTEGER,
            order_type TEXT, status TEXT, mode TEXT, created_at TEXT);
        CREATE TABLE approvals(decision_id INTEGER, account_id TEXT, order_json TEXT,
            status TEXT, requested_at TEXT, expires_at TEXT);
        CREATE TABLE positions_snapshot(account_id TEXT, date TEXT, ticker TEXT, market TEXT,
            qty INTEGER, created_at TEXT);
        CREATE TABLE fo_positions(account_id TEXT, code TEXT, kind TEXT, contract_side TEXT,
            direction TEXT, vehicle TEXT, status TEXT, created_at TEXT);
        """
    )
    return conn


def _load(p):
    return {a.id: a for a in load_accounts(p, secrets=Secrets())}


def _backups(p):
    return sorted(x.name for x in p.parent.glob(f"{p.name}.bak-*"))


def test_add_keeps_comments_and_backs_up(yml):
    r = accounts_io.add_account(
        yml, accounts_io.AccountSpec(id="real-2", env="real", markets=["KR", "US"])
    )
    assert r["id"] == "real-2" and r["backup"] in _backups(yml)
    text = yml.read_text(encoding="utf-8")
    assert "# 계좌 목록" in text and "# 실전은 off 로 시작" in text
    accts = _load(yml)
    assert list(accts) == ["paper-main", "real-main", "real-2"]
    new = accts["real-2"]
    assert new.mode == "off" and new.cano == "00000000" and new.acnt_prdt_cd == "01"
    assert new.markets == ["KR", "US"] and new.asset == "stock"


def test_add_real_forces_off_even_if_auto_requested(yml):
    accounts_io.add_account(yml, accounts_io.AccountSpec(id="real-3", env="real", mode="auto"))
    assert _load(yml)["real-3"].mode == "off"


def test_add_paper_defaults_auto(yml):
    accounts_io.add_account(yml, accounts_io.AccountSpec(id="paper-2", env="paper", markets=["US"]))
    a = _load(yml)["paper-2"]
    assert a.mode == "auto" and a.markets == ["US"]


def test_add_fo_fixes_product_and_markets(yml):
    accounts_io.add_account(
        yml, accounts_io.AccountSpec(id="paper-fo", env="paper", asset="fo", markets=["US"])
    )
    a = _load(yml)["paper-fo"]
    assert a.asset == "fo" and a.acnt_prdt_cd == "03" and a.markets == ["KR"]


@pytest.mark.parametrize(
    "bad", ["", "real main", "real_2", "-x", "x-", "a/b", "실전", "paper", "REAL"]
)
def test_add_rejects_bad_ids(yml, bad):
    before = yml.read_text(encoding="utf-8")
    with pytest.raises(ConfigError):
        accounts_io.add_account(yml, accounts_io.AccountSpec(id=bad, env="paper"))
    assert yml.read_text(encoding="utf-8") == before and _backups(yml) == []


@pytest.mark.parametrize("dup", ["paper-main", "PAPER-MAIN"])
def test_add_rejects_duplicates(yml, dup):
    with pytest.raises(ConfigError, match="이미"):
        accounts_io.add_account(yml, accounts_io.AccountSpec(id=dup, env="paper"))


def test_add_rejects_empty_markets(yml):
    with pytest.raises(ConfigError):
        accounts_io.add_account(yml, accounts_io.AccountSpec(id="p3", env="paper", markets=[]))


def test_add_into_empty_list(tmp_path):
    p = tmp_path / "accounts.yaml"
    p.write_text("# 비어 있음\naccounts: []\n", encoding="utf-8")
    accounts_io.add_account(p, accounts_io.AccountSpec(id="paper-main", env="paper"))
    assert list(_load(p)) == ["paper-main"]
    assert "# 비어 있음" in p.read_text(encoding="utf-8")


def test_add_restores_backup_when_validation_fails(yml, monkeypatch):
    before = yml.read_text(encoding="utf-8")

    # 스키마에 맞지 않는 줄이 쓰였다고 치면 다시 읽기가 실패해 백업으로 되돌린다
    monkeypatch.setattr(
        accounts_io, "_line", lambda spec, indent: f"{indent}- {{id: {spec.id}, env: weird}}"
    )
    with pytest.raises(ConfigError, match="되돌렸"):
        accounts_io.add_account(yml, accounts_io.AccountSpec(id="paper-2", env="paper"))
    assert yml.read_text(encoding="utf-8") == before


def test_remove_deletes_one_line_and_keeps_ledger(yml, db):
    db.execute(
        "INSERT INTO orders(account_id, ticker, market, side, qty, order_type, status, mode,"
        " created_at) VALUES ('real-main','005930','KR','BUY',1,'limit','filled','auto','t')"
    )
    db.commit()
    r = accounts_io.remove_account(yml, "real-main", db)
    assert r["id"] == "real-main" and r["backup"] in _backups(yml)
    assert list(_load(yml)) == ["paper-main"]
    text = yml.read_text(encoding="utf-8")
    assert "# 계좌 목록" in text and "# 실전은 off 로 시작" in text
    assert db.execute("SELECT COUNT(*) FROM orders").fetchone()[0] == 1


def test_remove_clears_mode_and_live_confirmation(yml, db):
    from trader.features.accounts.modes import set_account_mode

    acct = _load(yml)["real-main"]
    set_account_mode(db, acct, "auto", confirm="LIVE")
    q = "SELECT COUNT(*) FROM account_modes WHERE account_id=?"
    assert db.execute(q, ("real-main",)).fetchone()[0] == 1
    accounts_io.remove_account(yml, "real-main", db)
    assert db.execute(q, ("real-main",)).fetchone()[0] == 0


def test_remove_unknown_account(yml, db):
    with pytest.raises(ConfigError, match="없습니다"):
        accounts_io.remove_account(yml, "nope", db)


def _order(db, status):
    db.execute(
        "INSERT INTO orders(account_id, ticker, market, side, qty, order_type, status, mode,"
        " created_at) VALUES ('real-main','005930','KR','BUY',1,'limit',?,'auto','t')",
        (status,),
    )
    db.commit()


@pytest.mark.parametrize("status", ["accepted", "unknown", "partial"])
def test_remove_blocked_by_live_order(yml, db, status):
    _order(db, status)
    with pytest.raises(accounts_io.AccountInUse, match="주문"):
        accounts_io.remove_account(yml, "real-main", db)
    assert "real-main" in _load(yml) and _backups(yml) == []


def test_remove_allowed_with_dead_orders(yml, db):
    for s in ("rejected", "cancelled", "failed", "expired", "filled"):
        _order(db, s)
    accounts_io.remove_account(yml, "real-main", db)


def test_remove_blocked_by_pending_approval(yml, db):
    db.execute(
        "INSERT INTO approvals(decision_id, account_id, order_json, status, requested_at,"
        " expires_at) VALUES (1,'real-main','{}','pending','t','t')"
    )
    db.commit()
    with pytest.raises(accounts_io.AccountInUse, match="승인"):
        accounts_io.remove_account(yml, "real-main", db)


def _snap(db, date, ticker, qty, market="KR"):
    db.execute(
        "INSERT INTO positions_snapshot(account_id, date, ticker, market, qty, created_at)"
        " VALUES ('real-main',?,?,?,?,'t')",
        (date, ticker, market, qty),
    )
    db.commit()


def test_remove_blocked_by_latest_holding(yml, db):
    _snap(db, "2026-10-27", "005930", 0)
    _snap(db, "2026-10-28", "005930", 3)
    _snap(db, "2026-10-28", "_CASH_KR", 0)
    with pytest.raises(accounts_io.AccountInUse, match="보유"):
        accounts_io.remove_account(yml, "real-main", db)


def test_remove_allowed_when_latest_snapshot_flat(yml, db):
    _snap(db, "2026-10-27", "005930", 3)
    _snap(db, "2026-10-28", "_CASH_KR", 1)
    accounts_io.remove_account(yml, "real-main", db)


def _fo(db, status):
    db.execute(
        "INSERT INTO fo_positions(account_id, code, kind, contract_side, direction, vehicle,"
        " status, created_at) VALUES ('real-main','101W12','future','long','LONG','FUTURES',?,'t')",
        (status,),
    )
    db.commit()


def test_remove_blocked_by_open_fo_position(yml, db):
    _fo(db, "open")
    with pytest.raises(accounts_io.AccountInUse, match="파생"):
        accounts_io.remove_account(yml, "real-main", db)


def test_remove_allowed_with_closed_fo_positions(yml, db):
    _fo(db, "closed")
    _fo(db, "settled")
    accounts_io.remove_account(yml, "real-main", db)


def test_remove_rejects_block_style_entry(tmp_path, db):
    p = tmp_path / "accounts.yaml"
    p.write_text(
        'accounts:\n  - id: real-main\n    env: real\n    cano: "00000000"\n'
        '    markets: [KR]\n    mode: "off"\n',
        encoding="utf-8",
    )
    with pytest.raises(ConfigError, match="직접"):
        accounts_io.remove_account(p, "real-main", db)


def test_blockers_skip_missing_tables(yml, conn):
    """M4 전: 주문·승인·포지션 테이블이 없으면 그 사유는 보지 않고 삭제를 허용한다."""
    assert accounts_io.blockers(conn, "real-main") == []
    accounts_io.remove_account(yml, "real-main", conn)


def test_cli_add_and_remove(project):
    from typer.testing import CliRunner

    from trader.kernel.cli.main import app

    path = project / "config" / "accounts.yaml"
    path.write_text(YAML, encoding="utf-8")
    runner = CliRunner()
    r = runner.invoke(app, ["accounts", "add", "--id", "paper-2", "--env", "paper"])
    assert r.exit_code == 0, r.output
    assert r.stdout.startswith("추가: paper-2 (백업 accounts.yaml.bak-")
    assert "paper-2" in _load(path)
    r = runner.invoke(app, ["accounts", "remove", "paper-2"])
    assert r.exit_code == 0, r.output
    assert "paper-2" not in _load(path)
    r = runner.invoke(app, ["accounts", "remove", "nope"])
    assert r.exit_code == 1 and "오류:" in r.output
    r = runner.invoke(app, ["accounts", "add", "--id", "x y", "--env", "paper"])
    assert r.exit_code == 1 and "오류:" in r.output
