"""inputs 포트로 레인에 제공하는 공개 연구자료. 계좌·포지션·주문은 포함하지 않는다."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from trader.features.ingest import macro as macro_sources
from trader.features.ingest.base import owned_client
from trader.features.ingest.input_quality import activity_context, macro_context, source_coverage
from trader.features.ingest.naver_blog import NaverBlogSource
from trader.features.ingest.settings import SourcesConfig
from trader.kernel.config import Secrets


def collect_research_context(
    cfg: SourcesConfig,
    secrets: Secrets,
    *,
    conn: Any,
    market: str,
    asof: datetime,
    market_client: Any = None,
    http: Any = None,
    now=None,
    activity_port: Any = None,
    macro: dict | None = None,
) -> dict:
    """설정된 거시·공개 블로그와 asof 이전 KIS 활동을 공통 연구자료로 조립한다.

    이미 수집한 거시는 macro로 넘겨 중복 조회를 피한다. 블로그 언어로 대상 시장을 제한하지
    않는다. RSS 발행시각과 조회시각을 구분하고 실패는 값을 합성하지 않고 data_gaps에 남긴다.
    """
    now = now or (lambda: datetime.now(UTC))
    gaps: list[str] = []
    if macro is None:
        macro = {}
        if cfg.macro.enabled:
            try:
                macro = macro_sources.collect_macro(
                    cfg.macro, secrets, market_client=market_client, asof=asof, http=http, now=now
                )
            except Exception as exc:  # noqa: BLE001 - optional research evidence
                gaps.append(f"macro: {type(exc).__name__}: {exc}")
    macro = macro_context(macro, asof=asof)
    blogs = list(getattr(getattr(cfg, "channels", None), "naver_blog", []) or [])
    posts: list[dict] = []
    statuses = {}
    if blogs:
        try:
            with owned_client(http, timeout=10) as client:
                result = NaverBlogSource(
                    blogs,
                    http=client,
                    max_posts=getattr(cfg.channels, "naver_blog_posts", 5),
                    now=now,
                ).fetch(asof=asof)
            for source, items in result["sources"].items():
                posts.extend(items)
                statuses[source] = "ok"
            for source, error in result["errors"].items():
                statuses[source] = error
                gaps.append(f"{source}: {error}")
        except Exception as exc:  # noqa: BLE001 - public RSS is a supplemental input
            gaps.append(f"blogs: {type(exc).__name__}: {exc}")
    activity = activity_context(conn, market, asof=asof, port=activity_port)
    gaps.extend(activity.get("data_gaps") or [])
    return {
        "macro": macro,
        "market_activity": activity,
        "blog_posts": posts,
        "source_coverage": source_coverage(
            macro, community={"sources": statuses}, blog_posts=posts, blog_config=blogs
        ),
        "data_gaps": gaps,
    }
