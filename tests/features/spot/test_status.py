"""spot `/status`·STATUS.md 절: 최근 결정·미승인 주문·성향."""

from datetime import UTC, datetime

from tests.features.spot.approval_helpers import fake_brokers, make_account, make_pending, make_port
from trader.features.spot import status
from trader.features.spot.feature import FEATURE
from trader.features.spot.ledger import upsert_ticker_name
from trader.kernel.notify.commands import CommandContext

NOW = datetime(2026, 11, 5, 1, 0, tzinfo=UTC)  # 10:00 KST


class _Sent:
    def send(self, text, *, escape=True):
        return True


def _ctx(conn, settings, project, ports=None):
    return CommandContext(
        conn=conn, settings=settings, state=project / "state", now=lambda: NOW,
        notifier=_Sent(), features=[FEATURE], ports=ports or {},
    )  # fmt: skip


def _decision(conn, d, market, ticker, action):
    conn.execute(
        "INSERT INTO decisions(date, market, ticker, action, data_json, created_at) "
        "VALUES (?, ?, ?, ?, '{}', ?)",
        (d, market, ticker, action, NOW.isoformat()),
    )
    conn.commit()


def test_status_shows_last_decisions(conn, settings, project):
    ctx = _ctx(conn, settings, project)
    assert status.recent_decisions(ctx) == ["- 최근 결정 없음"]
    upsert_ticker_name(conn, "KR", "005930", "삼성전자", "test")
    _decision(conn, "2026-11-03", "US", "AAPL", "HOLD")
    _decision(conn, "2026-11-04", "KR", "000660", "SELL")
    _decision(conn, "2026-11-05", "KR", "005930", "BUY")
    _decision(conn, "2026-11-05", "US", "AAPL", "HOLD")
    assert status.recent_decisions(ctx) == [
        "- 2026-11-05 US AAPL HOLD",
        "- 2026-11-05 KR 삼성전자(005930) BUY",
        "- 2026-11-04 KR 000660 SELL",
    ]
    titles = [s.title for s in FEATURE.status_sections]
    assert titles == ["최근 결정", "미승인 주문", "성향"]


def test_status_shows_pending_and_stance(conn, settings, project):
    accounts = [make_account("paper-main", "paper", "approve", ["KR", "US"])]
    brokers = fake_brokers(accounts)
    assert status.pending_lines(_ctx(conn, settings, project)) == []
    assert status.stance_lines(_ctx(conn, settings, project)) == []  # 계좌 포트 없음
    aid = make_pending(conn, settings, accounts, brokers)
    ctx = _ctx(conn, settings, project, ports={"accounts": make_port(accounts, brokers)})
    pending = status.pending_lines(ctx)
    assert len(pending) == 1
    assert pending[0].startswith(f"- #{aid} paper-main(모의) 매수 ") and "005930" in pending[0]
    assert status.stance_lines(ctx) == ["- paper-main KR normal", "- paper-main US normal"]
