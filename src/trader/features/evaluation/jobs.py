"""evaluation 잡·훅: `scan_analyze.post` 섀도 기록 훅, 일일 리포트·모의 체결 점검 잡."""

from __future__ import annotations

import logging

from trader.features.accounts.api import split_accounts
from trader.features.evaluation import daily_report, paper_review, shadow
from trader.kernel.config import KST
from trader.kernel.registry.types import JobSpec
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


def _accounts(deps) -> list:
    port = (deps.ports or {}).get("accounts")
    stock, _ = split_accounts(list(port.accounts())) if port is not None else ([], [])
    return stock


def daily_report_body(deps, job) -> dict:
    """장 마감 일일 리포트 → 텔레그램(오늘 NAV 행이 있는 계좌만, 없으면 조용히). 체결 선동기화는
    하지 않는다(레인 호출 — US 는 실현손익이 하루 늦을 수 있다, README)."""
    conn = deps.conn_factory()
    try:
        r = daily_report.compute(
            conn,
            _accounts(deps),
            markets=[job.market],
            today_of=lambda m: run_store.run_date_for(m, deps.now()),
        )
    finally:
        conn.close()
    if r["items"]:
        deps.notifier.send(daily_report.message(r))
    return {"items": len(r["items"])}


def paper_review_body(deps, job=None) -> dict:
    """모의 체결 대 가정 주간 점검(토 09:00 KST): 지난 7일 결정 주문 체결률·슬리피지 → 텔레그램."""
    conn = deps.conn_factory()
    try:
        r = paper_review.weekly(conn, _accounts(deps), today=deps.now().astimezone(KST).date())
    finally:
        conn.close()
    if any(it["terminal"] for it in r["items"]) or r["alerts"]:
        deps.notifier.send(paper_review.message(r))
    return {"items": len(r["items"]), "alerts": len(r["alerts"])}


DAILY_REPORT = JobSpec(
    id="{market}_daily_report", kind="daily_report", trigger="cron", per_market=True,
    schedule_key="daily_report.{market}", interval_s=None, enabled=None, gate="market",
    heavy=False, misfire="default", catch_up="notify", start_soon_s=0, body=daily_report_body,
    days="mon-fri",
)  # fmt: skip

PAPER_REVIEW = JobSpec(
    id="paper_review", kind="paper_review", trigger="cron", per_market=False,
    schedule_key="paper_review", interval_s=None, enabled=None, gate="none", heavy=False,
    misfire="idempotent", catch_up="notify", start_soon_s=0, body=paper_review_body,
)  # fmt: skip
