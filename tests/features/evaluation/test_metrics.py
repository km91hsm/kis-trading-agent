from datetime import UTC, date, datetime

from tests.features.spot.helpers import ok_decision, ok_turn
from trader.features.evaluation import metrics
from trader.features.spot.ledger import ingest_checkpoint, insert_hedge_decision

D = date(2026, 10, 9)
NOW = datetime(2026, 10, 9, 6, 0, tzinfo=UTC)


def _decision(conn, ticker, action, d=D, market="KR"):
    ingest_checkpoint(
        conn, d, market, ticker, "30_decision", {"data": ok_decision(ticker=ticker, action=action)}
    )


def test_decision_distribution_counts_llm_only_by_market(conn):
    _decision(conn, "005930", "BUY")
    _decision(conn, "000660", "HOLD")
    _decision(conn, "035420", "HOLD")
    _decision(conn, "005380", "SELL", d=date(2026, 8, 1))  # 30일 밖
    insert_hedge_decision(conn, D, "KR", "069500", ok_decision(ticker="069500", action="SELL"))
    assert metrics.decision_distribution(conn, days=30, now=NOW) == {"BUY": 1, "HOLD": 2}
    assert metrics.decision_distribution(conn, days=365, now=NOW)["SELL"] == 1
    assert metrics.decision_distribution(conn, days=30, now=NOW, market="US") == {}


def test_convergence_rate_none_without_debates(conn):
    assert metrics.convergence_rate(conn, days=30, now=NOW) is None
    # 005930: 최종 라운드 양측 부호 확신 차이 ≤ 0.2 → 수렴
    for side, stance, conv in (("bull", "BULLISH", 0.6), ("bear", "BULLISH", 0.5)):
        ingest_checkpoint(
            conn, D, "KR", "005930", f"20_debate_r1_{side}",
            {"data": ok_turn(side, round=1, stance=stance, conviction=conv)},
        )  # fmt: skip
    # 000660: 라운드 0 만, 대립 → 비수렴
    for side, stance in (("bull", "BULLISH"), ("bear", "BEARISH")):
        ingest_checkpoint(
            conn, D, "KR", "000660", f"20_debate_r0_{side}",
            {"data": ok_turn(side, round=0, stance=stance, conviction=0.7)},
        )  # fmt: skip
    assert metrics.convergence_rate(conn, days=30, now=NOW) == 0.5


def test_convergence_counts_each_lane(conn):
    # 한 결정: 판 a 최종 gap 0.1(수렴), 판 b 최종 gap 0.9(비수렴) → 표본 2개 중 1개
    for stage, bull, bear in (("20_debate", 0.6, 0.5), ("20_debatex", 0.9, 0.0)):
        for side, conv in (("bull", bull), ("bear", bear)):
            ingest_checkpoint(
                conn, D, "KR", "005930", f"{stage}_r0_{side}",
                {"data": ok_turn(side, stance="BULLISH", conviction=conv)},
            )  # fmt: skip
    assert metrics.convergence_rate(conn, days=30, now=NOW) == 0.5


def test_convergence_definition_text():
    assert metrics.CONVERGENCE_DEFINITION == (
        "판(강세·약세 한 쌍) 단위 — 4인 교차 토론 결정은 표본 2개"
    )
