"""scan `/status`·STATUS.md 「오늘 스캔」 절."""

from dataclasses import replace
from datetime import UTC, datetime

from trader.features.scan import status
from trader.features.scan.feature import FEATURE
from trader.kernel.notify.commands import CommandContext
from trader.kernel.session.run_store import market_dir, run_date_for, write_checkpoint

NOW = datetime(2026, 11, 5, 1, 0, tzinfo=UTC)  # 10:00 KST


class _Sent:
    def send(self, text, *, escape=True):
        return True


def _ctx(conn, settings, project):
    return CommandContext(
        conn=conn, settings=settings, state=project / "state", now=lambda: NOW,
        notifier=_Sent(), features=[FEATURE],
    )  # fmt: skip


def test_status_scan_section(conn, settings, project):
    ctx = _ctx(conn, settings, project)
    assert [s.title for s in FEATURE.status_sections] == ["오늘 스캔"]
    assert status.scan_lines(ctx) == ["- 오늘 스캔 없음"]
    d = run_date_for("KR", NOW)
    scan = {
        "market": "KR",
        "date": d.isoformat(),
        "picks": [{"ticker": "005930"}],
        "analyze_targets": [{}, {}],
        "discovery": {"candidates": [{"tier": "universe"}], "unresolved": []},
    }
    write_checkpoint(market_dir(d, "KR", ctx.state, create=True), "_scan", scan)
    assert status.scan_lines(ctx) == [
        f"- KR {d.isoformat()}: 선정 1(005930) · 분석 대상 2 · "
        "뉴스 발굴 1(유니버스 1·밖 0·미해결 0)"
    ]
    assert status.scan_lines(replace(ctx, settings=None)) == []
