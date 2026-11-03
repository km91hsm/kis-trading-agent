"""데몬 잡 `news_collect`: 시장 전체 뉴스 저장소 수집·세이브티커 적재·보존 정리·훅."""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any

from trader.features.ingest import news_store
from trader.features.ingest.saveticker_ingest import ingest_news
from trader.features.ingest.settings import load_sources
from trader.features.ingest.universe import load_universe
from trader.features.saveticker.api import load_cfg, resolve_dir
from trader.kernel.config.secrets import Secrets
from trader.kernel.registry import hooks
from trader.kernel.registry.types import JobSpec

logger = logging.getLogger("trader.sources")


def _err(e: Exception) -> dict:
    return {"error": f"{type(e).__name__}: {e}"}


def news_collect(ctx: Any, job: Any) -> dict:
    """시장별 뉴스 질의를 돌려 `news_items` 에 쌓고, 세이브티커 폴더를 적재하고, 보존 기간이 지난
    행을 지운 뒤 `news.post_ingest` 훅을 부른다. 시장·세이브티커 실패는 그 항목만 error 로 남기고
    잡은 계속한다. 종목명 캐시(`ticker_names`)는 M7 에서 붙인다(지금은 빈 캐시)."""
    cfg = load_sources(registry=ctx.registry)
    if not cfg.news.enabled:
        return {"skipped": "news.enabled=false"}
    universe = load_universe()
    names_by_market = {m: news_store.universe_names(universe, {}, m) for m in ctx.markets}
    secrets = ctx.secrets or Secrets()
    out: dict[str, Any] = {}
    conn = ctx.conn_factory()
    try:
        for m in ctx.markets:
            if ctx.stop_event.is_set():
                break
            try:
                out[m] = news_store.collect(
                    conn, cfg.news, secrets, market=m, names=names_by_market[m], now=ctx.now
                )
            except Exception as e:  # noqa: BLE001 - 시장 단위 격리
                logger.warning("news_collect %s 실패: %s", m, e)
                out[m] = _err(e)
        try:
            root = resolve_dir(load_cfg(registry=ctx.registry).dir)
            if root is None:
                out["saveticker"] = {"skipped": "saveticker_dir 미설정"}
            else:
                out["saveticker"] = ingest_news(
                    conn, root, now=ctx.now, names_by_market=names_by_market
                )
        except Exception as e:  # noqa: BLE001
            logger.warning("news_collect saveticker 실패: %s", e)
            out["saveticker"] = _err(e)
        out["pruned"] = news_store.prune(
            conn, before=ctx.now() - timedelta(days=cfg.news.store_retention_days)
        )
        out["hooks"] = hooks.run(
            ctx.hooks, "news.post_ingest", conn=conn, markets=ctx.markets, now=ctx.now(),
            result=out, notifier=ctx.notifier,
        )
        return out
    finally:
        conn.close()


JOB = JobSpec(
    id="news_collect", kind="news_collect", trigger="interval", per_market=False,
    schedule_key="news_collect.interval_s", interval_s=1800, enabled=None, gate="none",
    heavy=False, misfire="default", catch_up="none", start_soon_s=0, body=news_collect,
)  # fmt: skip
