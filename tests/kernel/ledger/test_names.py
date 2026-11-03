"""kernel/ledger/names — 종목 표기·원장/캐시 이름 조회."""

import json

import pytest

from trader.kernel.ledger import db, names
from trader.kernel.registry import discover
from trader.kernel.registry.contracts import all_migrations


@pytest.fixture
def lconn(project):
    c = db.connect()
    db.init_schema(c, all_migrations(discover()))
    yield c
    c.close()


def _name(conn, ticker, name):
    conn.execute(
        "INSERT INTO ticker_names(market, ticker, name, source, updated_at) "
        "VALUES ('KR', ?, ?, 'dart', '2026-10-09T00:00:00+00:00')",
        (ticker, name),
    )
    conn.commit()


def test_label_and_resolve(lconn):
    _name(lconn, "005930", "삼성전자")
    assert names.resolve(lconn, "KR", "005930") == "삼성전자"
    assert names.resolve(lconn, "KR", "000660") is None
    assert names.label("005930", "삼성전자") == "삼성전자(005930)"
    assert names.label("005930", None) == "005930"


def test_load_cache_names_skips_identity_and_broken_file(tmp_path, caplog):
    (tmp_path / "dart_corpcode.json").write_text(
        json.dumps(
            {"map": {"005930": {"corp_name": "삼성전자"}, "000660": {"corp_name": "000660"}}}
        ),
        encoding="utf-8",
    )
    (tmp_path / "edgar_tickers.json").write_text("{not json", encoding="utf-8")
    with caplog.at_level("WARNING"):
        out = names.load_cache_names(tmp_path)
    assert out == {("KR", "005930"): "삼성전자"}
    assert "edgar_tickers.json" in caplog.text


def test_display_never_raises(lconn, tmp_path):
    (tmp_path / "dart_corpcode.json").write_text(
        json.dumps({"map": {"005930": {"corp_name": "삼성전자"}}}), encoding="utf-8"
    )
    assert names.display(lconn, tmp_path, "KR", "005930") == "삼성전자(005930)"  # 캐시
    _name(lconn, "000660", "SK하이닉스")
    assert names.display(lconn, tmp_path, "KR", "000660") == "SK하이닉스(000660)"  # 원장
    assert names.display(lconn, tmp_path, "KR", "999999") == "999999"
    lconn.close()  # 닫힌 연결 → 예외 대신 코드만
    assert names.display(lconn, tmp_path / "none", "KR", "999999") == "999999"
