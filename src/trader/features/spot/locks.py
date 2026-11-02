"""(시장, 종목) 결정 실행 락(Ruling P11). 같은 종목의 분석 세션(M7 `analyze_ticker`)과 재평가
세션이 겹치지 않게 한다 — 두 경로가 이 모듈의 `lock_for` 로 같은 락을 얻는다(옛 데몬
`_ticker_lock`). 레지스트리는 프로세스 수명 동안 유지된다."""

from __future__ import annotations

import threading

_LOCKS: dict[tuple[str, str], threading.Lock] = {}
_GUARD = threading.Lock()


def lock_for(market: str, ticker: str) -> threading.Lock:
    """(시장, 종목) 마다 하나인 락. 같은 키면 언제나 같은 객체."""
    with _GUARD:
        return _LOCKS.setdefault((market, str(ticker)), threading.Lock())
