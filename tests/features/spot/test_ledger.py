import json
from datetime import date
from decimal import Decimal

from tests.features.spot.conftest import D
from tests.features.spot.helpers import ok_decision, ok_report, ok_turn
from trader.features.spot import labels, ledger


def cp(data: dict, session_id: str | None = "s1") -> dict:
    return {"data": data, "call": {"agent": "x", "session_id": session_id}}


def test_ingest_checkpoint_is_idempotent(conn):
    ledger.ingest_checkpoint(conn, D, "KR", "005930", "10_analyst_technical", cp(ok_report()))
    ledger.ingest_checkpoint(
        conn, D, "KR", "005930", "10_analyst_technical", cp(ok_report(score=0.5))
    )
    rows = conn.execute("SELECT * FROM analyst_reports").fetchall()
    assert len(rows) == 1 and rows[0]["role"] == "technical" and rows[0]["score"] == 0.5
    assert rows[0]["decision_date"] == "2026-10-09"
    for _ in range(2):
        ledger.ingest_checkpoint(
            conn, D, "KR", "005930", "20_debate_r1_bear", cp(ok_turn("bear", 1))
        )
    t = conn.execute("SELECT * FROM debate_turns").fetchall()
    assert len(t) == 1 and (t[0]["round"], t[0]["side"], t[0]["stance"]) == (1, "bear", "BEARISH")
    for _ in range(2):
        ledger.ingest_checkpoint(conn, D, "KR", "005930", "30_decision", cp(ok_decision()))
    assert conn.execute("SELECT COUNT(*) FROM decisions").fetchone()[0] == 1
    r = conn.execute("SELECT * FROM decision_rounds").fetchall()
    assert len(r) == 1 and r[0]["round"] == 0 and r[0]["action"] == "BUY"
    ledger.ingest_checkpoint(conn, D, "KR", "005930", "00_inputs", cp({"a": 1}))  # 무시
    ledger.ingest_checkpoint(
        conn, D, "KR", "005930", "31_decision_r1", cp(ok_decision(action="HOLD"))
    )
    rounds = conn.execute("SELECT round, action FROM decision_rounds ORDER BY round").fetchall()
    assert [(x["round"], x["action"]) for x in rounds] == [(0, "BUY"), (1, "HOLD")]


def test_decision_id_and_get_decision_hydrate(conn):
    assert ledger.decision_id(conn, D, "KR", "005930") is None
    ledger.ingest_checkpoint(conn, D, "KR", "005930", "30_decision", cp(ok_decision()))
    did = ledger.decision_id(conn, D, "KR", "005930")
    assert did is not None
    row = ledger.get_decision(conn, did)
    assert row["action"] == "BUY" and row["target_weight"] == "0.1"
    assert row["limit_price"] == "71000.0" and row["horizon_days"] == 10
    assert "data" not in row and isinstance(row["data_json"], str)
    h = ledger.get_decision(conn, did, hydrate=True)
    assert h["data"] == json.loads(row["data_json"]) and h["data"]["rationale"]
    assert ledger.get_decision(conn, 999) is None


def test_set_skipped_debate_and_side(conn):
    ledger.ingest_checkpoint(conn, D, "KR", "005930", "30_decision", cp(ok_decision()))
    did = ledger.decision_id(conn, D, "KR", "005930")
    assert ledger.get_decision(conn, did)["skipped_debate"] == 0
    ledger.set_skipped_debate(conn, did, True)
    assert ledger.get_decision(conn, did)["skipped_debate"] == 1
    assert ledger.decision_side(conn, did) == "BUY"
    ledger.ingest_checkpoint(
        conn, date(2026, 10, 10), "KR", "000660", "30_decision", cp(ok_decision(action="TRIM"))
    )
    d2 = ledger.decision_id(conn, date(2026, 10, 10), "KR", "000660")
    assert ledger.decision_side(conn, d2) == "SELL"
    ledger.ingest_checkpoint(
        conn, date(2026, 10, 10), "KR", "035720", "30_decision", cp(ok_decision(action="HOLD"))
    )
    d3 = ledger.decision_id(conn, date(2026, 10, 10), "KR", "035720")
    assert ledger.decision_side(conn, d3) is None and ledger.decision_side(conn, 999) is None


def test_ticker_name_and_exchange(conn):
    ledger.upsert_ticker_name(conn, "KR", "005930", "삼성전자", "dart")
    ledger.upsert_ticker_name(conn, "KR", "005930", "삼성전자(신)", "cache")
    assert labels.resolve(conn, "KR", "005930") == "삼성전자(신)"
    assert labels.resolve(conn, "KR", "000660") is None
    assert labels.label("005930", "삼성전자") == "삼성전자(005930)"
    assert labels.label("005930", None) == "005930"
    ledger.upsert_ticker_name(conn, "KR", "000660", "", "dart")  # 빈 이름 무시
    assert labels.resolve(conn, "KR", "000660") is None
    ledger.upsert_ticker_exchange(conn, "US", "AAPL", "NAS")
    assert ledger.exchange_of(conn, "US", "AAPL") == "NAS"
    assert ledger.exchange_of(conn, "US", "MSFT") is None


def test_latest_decision(conn):
    assert ledger.latest_decision(conn, "KR", "005930") is None
    for d, w in ((date(2026, 10, 8), 0.05), (D, 0.1)):
        ledger.ingest_checkpoint(
            conn, d, "KR", "005930", "30_decision", cp(ok_decision(target_weight=w))
        )
    assert ledger.latest_decision(conn, "KR", "005930")["data"]["target_weight"] == 0.1
    prev = ledger.latest_decision(conn, "KR", "005930", before=D)
    assert prev["date"] == "2026-10-08" and prev["data"]["target_weight"] == 0.05
    assert ledger.latest_decision(conn, "KR", "005930", before=date(2026, 10, 8)) is None


def test_decision_upsert_keeps_row_id(conn):
    ledger.ingest_checkpoint(conn, D, "KR", "005930", "30_decision", cp(ok_decision()))
    a = ledger.decision_id(conn, D, "KR", "005930")
    ledger.ingest_checkpoint(conn, D, "KR", "005930", "30_decision", cp(ok_decision(action="ADD")))
    assert ledger.decision_id(conn, D, "KR", "005930") == a
    assert ledger.get_decision(conn, a)["action"] == "ADD"


def test_round0_stays_in_sync_with_decision_row(conn):
    ledger.ingest_checkpoint(conn, D, "KR", "005930", "30_decision", cp(ok_decision()))
    did = ledger.decision_id(conn, D, "KR", "005930")
    ledger.set_skipped_debate(conn, did, True)
    conn.execute("UPDATE decisions SET source='hedge' WHERE id=?", (did,))
    ledger.ingest_checkpoint(conn, D, "KR", "005930", "30_decision", cp(ok_decision(action="ADD")))

    def both():
        d = conn.execute("SELECT source, skipped_debate FROM decisions").fetchone()
        r = conn.execute(
            "SELECT source, skipped_debate, action FROM decision_rounds WHERE round=0"
        ).fetchone()
        return tuple(d), tuple(r)

    d, r = both()
    assert d == r[:2] == ("hedge", 1) and r[2] == "ADD"
    ledger.set_skipped_debate(conn, did, False)
    d, r = both()
    assert d[1] == r[1] == 0


def test_stop_loss_floor_survives_same_day_reevaluation(conn):
    """전체 검토 2차 2-O1: 매수 당일 BUY(손절 65000) 뒤 오후 재평가 `31_decision_r1` HOLD 가
    손절가를 비워도(같은 (date, market, ticker) 행을 덮어쓴다) 아침 손절선이 살아 있어야 한다."""
    first = cp(ok_decision(stop_loss=65000.0))
    ledger.ingest_checkpoint(conn, D, "KR", "005930", "30_decision", first)
    did = ledger.decision_id(conn, D, "KR", "005930")
    ledger.ingest_checkpoint(
        conn,
        D,
        "KR",
        "005930",
        "31_decision_r1",
        cp(ok_decision(action="HOLD", target_weight=0.0, stop_loss=None)),
    )
    assert ledger.get_decision(conn, did)["action"] == "HOLD"  # 행은 덮였다
    assert ledger.max_stop_since(conn, "KR", "005930", did) == Decimal("65000.0")
    r1 = conn.execute("SELECT stop_loss FROM decision_rounds WHERE round=1").fetchone()
    assert Decimal(r1["stop_loss"]) == Decimal("65000")  # 라운드 행 손절가가 바닥 이상으로 올라간다
    # 더 높은 손절가는 그대로 두고, SELL 은 바닥에 넣지도 올리지도 않는다
    ledger.ingest_checkpoint(
        conn, D, "KR", "005930", "31_decision_r2", cp(ok_decision(action="ADD", stop_loss=68000.0))
    )
    ledger.ingest_checkpoint(
        conn,
        D,
        "KR",
        "005930",
        "31_decision_r3",
        cp(ok_decision(action="SELL", target_weight=0.0, stop_loss=1000.0)),
    )
    assert ledger.max_stop_since(conn, "KR", "005930", did) == Decimal("68000.0")
    r3 = conn.execute("SELECT stop_loss FROM decision_rounds WHERE round=3").fetchone()
    assert Decimal(r3["stop_loss"]) == Decimal("1000")
