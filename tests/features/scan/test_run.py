"""`run_scan`·`scan_line`·screener 세션 실패 폴백."""

import json
from decimal import Decimal

from tests.features.accounts.conftest import make_account
from tests.features.scan.conftest import D, Sent, lane
from trader.features.scan import run, screener
from trader.kernel.broker.fake import FakeBroker
from trader.kernel.broker.models import Position
from trader.kernel.session.runner import SessionResult

CANDS = [
    {"ticker": f"0000{i:02d}", "exchange": None, "score": 10 - i, "reasons": ["r"]}
    for i in range(8)
]


def test_scan_line_literal():
    line = run.scan_line(
        {
            "market": "KR",
            "date": "2026-10-08",
            "picks": [{"ticker": "005930"}, {"ticker": "247540"}],
            "analyze_targets": [{}, {}, {}],
            "discovery": {"candidates": [{"tier": "listed"}], "unresolved": [{"name": "x"}]},
            "cap_hit": True,
        }
    )
    assert line == (
        "[스캔] KR 2026-10-08: 선정 2(005930, 247540) · 분석 대상 3 · "
        "뉴스 발굴 1(유니버스 0·밖 1·미해결 1) · 세션 상한"
    )


def test_scan_line_accepts_already_summarized_discovery():
    """`run_screen` 이 돌려주는 요약(미해결이 개수)을 `_scan` 에 담아 그대로 알린다 — 옛 코드는 이
    경로에서 TypeError 로 알림이 조용히 사라졌다."""
    from trader.features.scan import discovery

    disc = discovery.summary(
        {"candidates": [{"ticker": "000660", "tier": "universe"}], "unresolved": [{"name": "x"}]}
    )
    line = run.scan_line({"market": "KR", "date": "2026-10-08", "discovery": disc})
    assert "뉴스 발굴 1(유니버스 1·밖 0·미해결 1)" in line


def test_run_scan_writes_raw_scan_checkpoint_and_notifies(project, conn, settings, monkeypatch):
    monkeypatch.setenv("FAKE_CLAUDE", "1")
    monkeypatch.setattr(run.quant, "candidates", lambda *a, **k: CANDS)
    sent = Sent()
    out = run.run_scan(lane(conn, settings, notifier=sent), market="KR", dry_run=True)
    f = project / "state" / "runs" / "2026-10-09" / "KR" / "_scan.json"
    raw = json.loads(f.read_text(encoding="utf-8"))
    assert "schema_version" not in raw and raw["picks"] == out["picks"]  # 봉투 없는 raw
    assert raw["market"] == "KR" and raw["date"] == "2026-10-09" and raw["cap_hit"] is True
    assert raw["ml_model"] is None and raw["session"] is None
    assert sent.sent == [run.scan_line(out)]
    # 알림 실패는 결과를 바꾸지 않는다
    again = run.run_scan(lane(conn, settings, notifier=Sent(fail=True)), market="KR", dry_run=True)
    assert again["picks"] == out["picks"]


def test_run_screen_session_failure_falls_back_to_top3_and_keeps_held_watch(
    project, conn, settings, monkeypatch
):
    """Review Focus 4: 세션이 실패하면 정량 상위 3개(screener 실패)로, 보유·watch 는 그대로."""
    calls = []

    def boom(spec, kind, ctx, **kw):
        calls.append(spec.kind)
        return SessionResult(ok=False, error="boom")

    monkeypatch.setattr(screener, "run_session", boom)
    acct = make_account("paper-main", "paper", "auto")
    pos = Position(
        ticker="000007", market="KR", qty=1, avg_price=Decimal(1), current_price=Decimal(1),
        eval_amount=Decimal(1), pnl_pct=0.0,
    )  # fmt: skip
    lc = lane(
        conn, settings, accounts=[acct], brokers={"paper-main": FakeBroker(acct, positions=[pos])}
    )
    r = screener.run_screen(
        lc, market="KR", date=D, candidates=CANDS, cap_hit=False, watch=["KR:005930"]
    )
    assert calls == ["screen"]
    assert r["picks"] == [
        {"ticker": t, "reason": "screener 실패: 정량 상위"} for t in ("000000", "000001", "000002")
    ]
    assert [(t["ticker"], t["source"]) for t in r["analyze_targets"]] == [
        ("000000", "pick"), ("000001", "pick"), ("000002", "pick"),
        ("000007", "held"), ("005930", "watch"),
    ]  # fmt: skip
    assert r["session"] == {"ok": False, "fake": False, "cost_usd": None}


def test_run_screen_ok_without_screen_checkpoint_falls_back_to_top3(
    project, conn, settings, monkeypatch
):
    """Review Focus 4: 세션은 ok=True 인데 `_screen` 체크포인트가 없으면(빈 picks) 정량 상위 3개로,
    watch 는 그대로."""
    monkeypatch.setattr(
        screener, "run_session", lambda spec, kind, ctx, **kw: SessionResult(ok=True)
    )
    lc = lane(conn, settings)
    r = screener.run_screen(
        lc, market="KR", date=D, candidates=CANDS, cap_hit=False, watch=["KR:005930"]
    )
    assert r["picks"] == [
        {"ticker": t, "reason": "screener 실패: 정량 상위"} for t in ("000000", "000001", "000002")
    ]
    assert ("005930", "watch") in [(t["ticker"], t["source"]) for t in r["analyze_targets"]]


def _capture_screen(monkeypatch):
    got = {}

    def fake_screen(ctx, **kw):
        got.update(kw)
        got["closed_at_call"] = getattr(kw["http"], "is_closed", None)
        return {"picks": [], "analyze_targets": [], "discovery": None, "session": None}

    monkeypatch.setattr(run.screener, "run_screen", fake_screen)
    monkeypatch.setattr(run.quant, "candidates", lambda *a, **k: CANDS)
    return got


def test_run_scan_passes_injected_http_to_run_screen(project, conn, settings, monkeypatch):
    got = _capture_screen(monkeypatch)
    lc = lane(conn, settings)
    marker = object()
    lc.ext["http"] = marker
    run.run_scan(lc, market="KR", dry_run=True)
    assert got["http"] is marker


def test_run_scan_builds_and_closes_default_http_client(project, conn, settings, monkeypatch):
    import httpx

    got = _capture_screen(monkeypatch)
    run.run_scan(lane(conn, settings), market="KR", dry_run=True)
    client = got["http"]
    assert isinstance(client, httpx.Client) and got["closed_at_call"] is False
    assert client.is_closed


def test_cli_scan_run_bad_asof_exits_1(project):
    from typer.testing import CliRunner

    from trader.kernel.cli.main import app

    r = CliRunner().invoke(app, ["scan", "run", "--market", "KR", "--asof", "nope"])
    assert r.exit_code == 1 and "오류" in r.output
