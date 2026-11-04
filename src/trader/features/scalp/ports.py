"""scalp 포트: 다른 레인이 `Boot.ports["scalp"]` 로 받는 단타 조회 구현체
(커널 `execution.ports.ScalpPort`) — spot 리밸런스가 단타 보유분을 건드리지 않게 한다(A9)."""

from __future__ import annotations

import logging
from datetime import date
from pathlib import Path
from typing import Any

from trader.features.scalp.state import load_state
from trader.kernel.registry.types import Port

logger = logging.getLogger("trader.scalp")


class ScalpPortImpl:
    """오늘 단타 상태 파일(`state/scalp/<date>/<market>.json`)의 열린 포지션만 읽는다."""

    def open_tickers(self, state: Path | None, market: str, today: date) -> set[str]:
        """오늘 단타가 들고 있는 종목 집합. 상태 파일을 못 읽으면 빈 집합."""
        try:
            st = load_state(state, today, market)
            return {str(p["ticker"]) for p in st.get("open") or [] if p.get("ticker")}
        except Exception as e:  # noqa: BLE001 - 조회 실패가 다른 레인을 멈추지 않는다
            logger.warning("단타 보유 조회 실패 %s: %s", market, e)
            return set()


def build_port(boot: Any) -> ScalpPortImpl:
    return ScalpPortImpl()


PORT = Port("scalp", build_port)
