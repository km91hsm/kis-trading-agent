"""evaluation 잡·훅. 이 파일은 `scan_analyze.post` 섀도 기록 훅부터 둔다."""

from __future__ import annotations

import logging

from trader.features.evaluation import shadow
from trader.kernel.session import run_store
from trader.kernel.session.runner import is_fake

logger = logging.getLogger("trader.evaluation")


def shadow_record_hook(*, deps, market: str, result, **_) -> None:
    """섀도 포트폴리오(전체 검토 L1): 오늘 scan 후보·LLM 결정으로 비교 포트폴리오를 남긴다.
    주문 없음, 실패해도 잡은 계속(경고만). 전부 실패·중단·scan 없음인 날과 FAKE 세션은 남기지
    않는다 — 운영 실패나 고정 HOLD 를 LLM 선택으로 채점하지 않게(PR #33). 일부 종목만 실패한 날은
    성공한 결정으로 기록하되 실패 종목은 후보 기준선에서도 뺀다(LLM 이 고를 수 없었던 종목 —
    같은 후보 집합 비교, 2-L6)."""
    a = result if isinstance(result, dict) else {}
    failed = {str(t) for t in (a.get("failed") or [])}
    if a.get("skipped") or a.get("stopped") or a.get("error") or (failed and not a.get("ok")):
        logger.info("섀도 기록 생략 %s: 분석 미완(%s)", market, a)
        return
    try:
        if failed:
            logger.info("섀도 기록 %s: 실패 종목 제외 %s", market, ", ".join(sorted(failed)))
        now = deps.now()
        today = run_store.run_date_for(market, now)
        scan = run_store.read_checkpoint(run_store.market_dir(today, market, deps.state), "_scan")
        if not scan or is_fake() or ((scan.get("session") or {}).get("fake")):
            return
        accounts = (deps.ports or {}).get("accounts")
        client = accounts.market_client() if accounts is not None else None
        conn = deps.conn_factory()
        try:
            index = shadow.index_table(client, deps.settings.hedge, market, now=now)
            shadow.record(
                conn,
                market=market,
                day=today,
                scan=scan,
                now=now,
                index=index,
                exclude=frozenset(failed),
            )
        finally:
            conn.close()
    except Exception as e:  # noqa: BLE001 - 측정 실패가 분석 잡을 실패로 만들지 않는다
        logger.warning("섀도 포트폴리오 기록 실패 %s: %s", market, e)
