"""`/status`·STATUS.md 의 「오늘 스캔」 절: 시장별 오늘 `_scan` 체크포인트 한 줄."""

from __future__ import annotations

from typing import Any

from trader.features.scan.run import scan_body
from trader.kernel.registry.types import StatusSection
from trader.kernel.session.run_store import market_dir, read_checkpoint, run_date_for


def scan_lines(ctx: Any) -> list[str]:
    """시장마다 오늘(시장 운영일) `_scan` 이 있으면 한 줄. 하나도 없으면 '오늘 스캔 없음'.
    설정이 없는 문맥(settings=None)이면 시장 목록을 몰라 줄을 내지 않는다."""
    if ctx.settings is None:
        return []
    out = []
    for m in ctx.settings.markets:
        d = run_date_for(m, ctx.now())
        scan = read_checkpoint(market_dir(d, m, ctx.state), "_scan")
        if scan:
            out.append(f"- {scan_body(scan)}")
    return out or ["- 오늘 스캔 없음"]


STATUS_SECTION = StatusSection("오늘 스캔", scan_lines)
