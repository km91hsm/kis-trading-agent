"""단타 집행 엔진: 모의 전용·bear/RISK_OFF 금지·종목당 1회·dry_run·묶음 알림·종목별 오류 격리."""

from datetime import timedelta
from decimal import Decimal

from tests.features.scalp.conftest import NOW, D, Notes, bars, scan_checkpoint
from trader.features.accounts import api as accounts_api
from trader.features.accounts import modes
from trader.features.regime import regime as regime_mod
from trader.features.regime import tools as regime_tools
from trader.features.scalp import engine
from trader.features.scalp.state import load_state
from trader.features.scan import watchlist
from trader.kernel.broker.models import Position
from trader.kernel.ledger.control import set_flag
from trader.kernel.session.run_store import read_checkpoint, run_dir

PAPER_QTY = {"paper-main": 49, "paper-two": 49}  # 1천만 × 5% / 10050 지정가 — 모의 계좌마다


def test_tick_enters_on_signal_paper_only_and_once_per_ticker(scalp_ctx, market, conn, project):
    state = project / "state"
    scan_checkpoint(project, "KR", ["005930", "000660"])
    market.minutes.update({"005930": bars(burst=True), "000660": bars(burst=False, breakout=False)})
    ctx = scalp_ctx()
    out = engine.tick(ctx, market="KR", today=D)
    assert out["candidates"] == 2 and [e["ticker"] for e in out["entries"]] == ["005930"]
    assert out["entries"][0]["qty"] == PAPER_QTY
    st = load_state(state, D, "KR")
    assert st["open"][0]["ticker"] == "005930" and st["done"] == ["005930"]
    # 실전 계좌는 건너뛴다: 체크포인트는 모의 계좌만
    d = run_dir(D, "KR", "005930", state)
    assert read_checkpoint(d, "47_scalp_entry_paper-main")["data"]["approved"] is True
    assert read_checkpoint(d, "47_scalp_entry_real-1") is None
    assert read_checkpoint(d, "57_order_scalp_entry_paper-main")["data"]["status"] == "accepted"
    # decisions 행은 만들지 않는다 (오늘 LLM 결정과 키가 겹치므로)
    assert conn.execute("SELECT COUNT(*) FROM decisions").fetchone()[0] == 0
    row = conn.execute("SELECT decision_id, side, qty, origin FROM orders").fetchone()
    assert row["decision_id"] is None and row["side"] == "BUY" and row["origin"] == "scalp"
    # 그날 첫 묶음은 바로 — 진입은 '[단타]' 묶음 한 통 안의 한 줄
    assert [m.splitlines()[1] for m in ctx.notifier.msgs if m.startswith("[단타]")] == [
        "진입 005930 @10,050원 · " + load_state(state, D, "KR")["open"][0]["reason"]
    ]
    # 다음 틱: 같은 종목은 보유 중이라 후보에서 빠지고, 신호가 있어도 다시 사지 않는다
    out2 = engine.tick(ctx, market="KR", today=D)
    assert out2["entries"] == [] and out2["candidates"] == 1


def test_allowed_blocks_disabled_paused_bear_and_strategist(
    scalp_ctx, settings, conn, ports, accounts
):
    off = settings.scalp.model_copy(update={"enabled": False})
    ctx = scalp_ctx(settings=settings.model_copy(update={"scalp": off}))
    assert engine.allowed(ctx, "KR", D, {}) == "scalp.enabled=false"
    ctx = scalp_ctx()
    modes.set_paused(conn, True)
    assert engine.allowed(ctx, "KR", D, {}) == "일시정지"
    modes.set_paused(conn, False)
    set_flag(conn, f"{regime_mod.FLAG_PREFIX}KR", {"state": "bear", "count": 3})
    assert "bear" in engine.allowed(ctx, "KR", D, {})
    set_flag(conn, f"{regime_mod.FLAG_PREFIX}KR", {"state": "neutral", "count": 1})
    view_key = f"{regime_tools.MARKET_VIEW_FLAG}KR"
    view = {"date": D.isoformat(), "stance": "NEUTRAL", "confidence": 0.9, "scalp_ok": False}
    set_flag(conn, view_key, view)
    assert "scalp_ok" in engine.allowed(ctx, "KR", D, {})
    set_flag(conn, view_key, view | {"scalp_ok": True, "stance": "RISK_OFF"})
    assert "RISK_OFF" in engine.allowed(ctx, "KR", D, {})
    set_flag(conn, view_key, view | {"scalp_ok": False, "confidence": 0.3})
    assert engine.allowed(ctx, "KR", D, {}) is None  # 확신 미달 제안은 무시
    # 동시 보유 상한(A7 끝): 열린 단타가 max_positions 이면 신규 진입 금지
    full = {"open": [{"ticker": "a"}, {"ticker": "b"}]}
    assert engine.allowed(ctx, "KR", D, full) == "동시 보유 2/2"
    # 모의 계좌가 없으면 금지
    ports["accounts"].accounts = lambda: [a for a in accounts if a.env == "real"]
    assert "모의" in engine.allowed(ctx, "KR", D, {})


def test_candidates_exclude_held_hedge_and_include_news_watch(
    scalp_ctx, brokers, conn, project, ports
):
    import json

    scan_checkpoint(project, "KR", ["005930", "000660"])
    ctx = scalp_ctx()
    pos = Position(
        ticker="000660",
        market="KR",
        qty=1,
        avg_price=Decimal(1),
        current_price=Decimal(1),
        eval_amount=Decimal(1),
        pnl_pct=0.0,
    )
    brokers["paper-main"].positions = [pos]
    conn.execute(
        "INSERT INTO job_requests(kind, params, status, source, requested_at) "
        "VALUES ('analyze_ticker', ?, 'queued', 'news', ?)",
        (json.dumps({"market": "KR", "ticker": "247540"}), NOW.isoformat()),
    )
    conn.commit()
    watchlist.set_watchlist(conn, ["KR:035420", "US:AAPL"])
    hedge_t = next(iter(ports["regime"].hedge_tickers("KR")))
    watchlist.set_watchlist(conn, ["KR:035420", f"KR:{hedge_t}"])
    cands = engine.candidates(ctx, "KR", D, {"open": [], "done": ["247540"]})
    assert [c["ticker"] for c in cands] == ["005930", "035420"]


def test_dry_run_records_without_orders(scalp_ctx, market, conn, project):
    state = project / "state"
    scan_checkpoint(project, "KR", ["005930"])
    market.minutes["005930"] = bars(burst=True)
    ctx = scalp_ctx(dry_run=True)
    out = engine.tick(ctx, market="KR", today=D)
    assert out["entries"][0]["qty"] == PAPER_QTY
    assert conn.execute("SELECT COUNT(*) FROM orders").fetchone()[0] == 0
    assert load_state(state, D, "KR")["open"][0]["dry_run"] is True
    rep = engine.report(state, market="KR", days=3, today=D)
    assert rep["days"][0]["date"] == D.isoformat() and rep["summary"]["trades"] == 0


def test_digest_every_min_bundles_and_dry_run_is_korean(scalp_ctx, market, project):
    """묶음 주기가 지나면 모인 알림을 한 통으로, dry_run 은 '주문 없이 판정'."""
    scan_checkpoint(project, "KR", ["005930", "000660"])
    market.minutes["005930"] = bars(burst=True)
    ctx = scalp_ctx(dry_run=True)
    engine.tick(ctx, market="KR", today=D)
    assert len(ctx.notifier.msgs) == 1 and "(주문 없이 판정)" in ctx.notifier.msgs[0]
    assert "dry-run" not in ctx.notifier.msgs[0]
    market.minutes["000660"] = bars(burst=True)
    ctx.now = lambda: NOW + timedelta(minutes=10)
    engine.tick(ctx, market="KR", today=D)
    assert len(ctx.notifier.msgs) == 1  # 주기 안 — 보류
    ctx.now = lambda: NOW + timedelta(minutes=31)
    engine.tick(ctx, market="KR", today=D)
    assert len(ctx.notifier.msgs) == 2 and "진입 000660" in ctx.notifier.msgs[1]


def test_digest_kept_when_send_fails(scalp_ctx, market, project):
    """텔레그램 실패(False)·예외면 묶음을 버리지 않고 다음 틱에 다시 보낸다."""
    state = project / "state"
    scan_checkpoint(project, "KR", ["005930"])
    market.minutes["005930"] = bars(burst=True)
    ctx = scalp_ctx(notifier=Notes(ret=False))
    engine.tick(ctx, market="KR", today=D)
    st = load_state(state, D, "KR")
    assert len(st["outbox"]) == 1 and not st.get("digest_at")
    sent = []
    ctx.notifier.send = lambda text, *, escape=True: sent.append(text) or True
    ctx.now = lambda: NOW + timedelta(minutes=1)
    engine.tick(ctx, market="KR", today=D)
    assert len(sent) == 1 and "진입 005930" in sent[0]
    assert load_state(state, D, "KR")["outbox"] == []


# --- 신규 (Review Focus 1·3) --------------------------------------------------------------


def test_minute_fetch_failure_isolated_per_ticker(scalp_ctx, market, project, monkeypatch):
    """Review Focus 1: 한 종목의 분봉 조회 예외가 다른 후보 평가를 막지 않고 errors 로 남는다."""
    scan_checkpoint(project, "KR", ["005930", "000660"])
    market.minutes.update({"005930": bars(burst=True), "000660": bars(burst=True)})
    real = market.get_minute_kr

    def flaky(ticker, *, count=30):
        if ticker == "005930":
            raise RuntimeError("분봉 조회 실패")
        return real(ticker, count=count)

    monkeypatch.setattr(market, "get_minute_kr", flaky)
    out = engine.tick(scalp_ctx(), market="KR", today=D)
    assert [e["ticker"] for e in out["entries"]] == ["000660"]
    assert out["errors"] == [{"ticker": "005930", "error": "분봉 조회 실패"}]
    st = load_state(project / "state", D, "KR")
    assert [p["ticker"] for p in st["open"]] == ["000660"]


def test_real_account_never_gets_scalp_legs(scalp_ctx, market, conn, project, accounts):
    """Review Focus 3: 실전 계좌는 LIVE 확인된 auto 여도 단타 다리·주문·체크포인트가 없다."""
    real = next(a for a in accounts if a.id == "real-1")
    modes.set_account_mode(conn, real, "auto", confirm=accounts_api.LIVE_CONFIRM)
    assert accounts_api.effective_mode(conn, real)[0] == "auto"  # 모드가 막는 게 아니다
    scan_checkpoint(project, "KR", ["005930"])
    market.minutes["005930"] = bars(burst=True)
    out = engine.tick(scalp_ctx(), market="KR", today=D)
    assert out["entries"] and "real-1" not in out["entries"][0]["qty"]
    ids = {r["account_id"] for r in conn.execute("SELECT account_id FROM orders")}
    assert ids == {"paper-main", "paper-two"}
    d = run_dir(D, "KR", "005930", project / "state")
    assert read_checkpoint(d, "47_scalp_entry_real-1") is None
    assert read_checkpoint(d, "57_order_scalp_entry_real-1") is None
    pos = load_state(project / "state", D, "KR")["open"][0]
    assert "real-1" not in pos["legs"]
