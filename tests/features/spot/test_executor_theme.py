"""spot 게이트의 `ports["scan"]` 배선: 사건·테마 소속 → 테마 매매 안전장치.

옛 `tests/scan/test_events.py::test_shock_event_ticker_gets_theme_safeguard_in_gate` 이식
(치환: `config.Settings()`→`settings` 픽스처, `executor._theme_of(ctx, …)` 의 포트는
`ports["scan"]`). 상한 거부는 M6 신규."""

from datetime import date
from decimal import Decimal

from tests.features.spot.conftest import D, ectx, make_account
from tests.features.spot.helpers import ok_decision
from trader.features.accounts.settings import to_broker_account
from trader.features.scan import events
from trader.features.spot import executor
from trader.features.spot import ledger as spot_ledger
from trader.features.spot.schemas import Decision
from trader.kernel.broker.fake import FakeBroker
from trader.kernel.broker.models import Cash, Position
from trader.kernel.ledger import control

TODAY = date(2026, 10, 26)


def test_shock_event_ticker_gets_theme_safeguard_in_gate(conn, project, settings, ports):
    """오늘 감지된 shock 사건의 수혜 종목은 게이트에서 테마 규칙(보유 ≤10일·익절 필수)을 받는다."""
    events.record(
        conn,
        "KR",
        TODAY,
        [{"id": "war_geopolitics", "label": "전쟁", "kind": "shock", "hits": 3, "news": [],
          "tickers": [{"ticker": "012450", "name": "한화에어로스페이스", "role": "방산"}]}],
    )  # fmt: skip
    (project / "config" / "events.yaml").write_text("enabled: true\n", encoding="utf-8")
    ctx = ectx(ports, settings, conn, [], {})
    theme_of = executor._theme_of(ctx, "KR", TODAY)
    assert theme_of is not None and theme_of("012450") == ["사건:전쟁"]
    assert theme_of("005930") is None
    # 끄면 그날 기록된 사건도 게이트에 적용하지 않는다
    (project / "config" / "events.yaml").write_text("enabled: false\n", encoding="utf-8")
    off = executor._theme_of(ctx, "KR", TODAY)
    assert off is None or off("012450") is None


def test_theme_cap_rejects_second_theme_buy_over_max_theme_pct(project, conn, settings, ports):
    """같은 상위 테마 보유분이 이미 있으면, 그 테마 매수는 주문 뒤 합계가 max_theme_pct 를 넘을 때
    거부된다(테마 밖 종목은 그대로 통과)."""
    control.set_flag(
        conn,
        "theme_heat:KR",
        {
            "market": "KR",
            "date": D.isoformat(),
            "observed_at": "2026-10-09T00:30:00+00:00",
            "themes": [
                {"theme": "섹터:반도체", "rank": 1, "score": 0.9, "members": ["005930", "000660"]}
            ],
        },
    )
    acct = make_account("paper-main", "paper", "auto", markets=["KR"])
    held = Position(
        ticker="000660", market="KR", qty=200, avg_price=Decimal("7500"),
        current_price=Decimal("7500"), eval_amount=Decimal("1500000"), pnl_pct=0.0,
    )  # fmt: skip
    cash = {"KR": Cash(currency="KRW", available=Decimal("8500000"), total=Decimal("8500000"))}
    broker = FakeBroker(to_broker_account(acct), cash=cash, positions=[held])
    ctx = ectx(ports, settings, conn, [acct], {acct.id: broker})
    dec = Decision(**ok_decision(target_weight=0.1))
    data = {"data": dec.model_dump()}
    spot_ledger.ingest_checkpoint(conn, D, "KR", dec.ticker, "30_decision", data)
    res = executor.execute(ctx, dec, date=D, market="KR", price=Decimal("70000"), dry_run=True)
    paper = res[0]
    assert not paper["approved"]
    assert any("테마 '섹터:반도체' 비중" in r and "상한" in r for r in paper["reasons"])
