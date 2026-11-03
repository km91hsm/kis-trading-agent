"""scan 세션 도구 구현: `trader tool scan discovery-inputs`·`submit-discovery`·`submit-screen`.

시장 단위 체크포인트 `_discovery_inputs`(파이썬이 세션 앞에서)·`_discovery`·`_screen` 을
`state/runs/<날짜>/<시장>/` 에 둔다. 모든 함수는 예외 대신 {"ok": bool, ...} dict 를 돌려준다."""

from __future__ import annotations

import logging
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from trader.features.ingest.api import Universe, load_universe
from trader.features.regime import api as regime_api
from trader.features.scan import discovery, schemas
from trader.kernel.ledger import names
from trader.kernel.platform import paths
from trader.kernel.session import tooling
from trader.kernel.session.run_store import checkpoint_payload as _wrap
from trader.kernel.session.run_store import market_dir, read_checkpoint, write_checkpoint

logger = logging.getLogger("trader.scan")

BUNDLE_LIMIT_BYTES = 24_000  # Claude Code Bash 출력 한도(30KB, UTF-8 바이트) 아래

# 공용 헬퍼는 kernel/session/tooling 이 정본 — 옛 밑줄 이름 호환 별칭
_unescape = tooling.unescape
_call = tooling.call_meta
_shrink = tooling.shrink


@dataclass
class ScanToolContext:
    """scan 도구의 실행 컨텍스트(계좌·브로커는 쓰지 않는다)."""

    settings: Any
    conn: sqlite3.Connection | None = None
    state: Path | None = None
    now: Callable[[], datetime] = field(default=lambda: datetime.now(UTC))
    market_client: Any | None = None  # 발굴: 유니버스 밖 종목의 시세·거래소 확인
    registry: Any | None = None


def tool_ctx(boot: Any) -> ScanToolContext:
    """부트 결과(`ports=True`)로 ScanToolContext 를 만든다. 시세 클라이언트가 없으면 None."""
    acc = (boot.ports or {}).get("accounts")
    return ScanToolContext(
        settings=boot.settings,
        conn=boot.conn,
        state=paths.state_dir(),
        market_client=acc.market_client() if acc is not None else None,
        registry=getattr(boot, "registry", None),
    )


# --- submit-screen (Phase 6 scan) ---------------------------------------------


def submit_screen(
    ctx: ScanToolContext,
    *,
    market: str,
    date: date,
    payload: dict,
    agent: str = "screener",
    call_meta: dict | None = None,
) -> dict:
    """Screened 검증 후 state/runs/<date>/<market>/_screen.json 저장."""
    if market not in ("KR", "US"):
        return {"ok": False, "error": f"market 은 KR 또는 US: {market!r}"}
    try:
        screened = schemas.Screened.model_validate(_unescape(payload))
    except ValidationError as e:
        msgs = "; ".join(
            f"{'.'.join(str(x) for x in err['loc'])}: {err['msg']}" for err in e.errors()
        )
        return {"ok": False, "error": f"스키마 검증 실패(Screened): {msgs}"}
    d = market_dir(date, market, ctx.state, create=True)
    path = write_checkpoint(d, "_screen", _wrap(screened.model_dump(), _call(agent, call_meta)))
    logger.info("스크리닝 저장 %s: %s", market, [p.ticker for p in screened.picks])
    return {"ok": True, "path": str(path), "picks": [p.ticker for p in screened.picks]}


# --- 뉴스 발굴 (Phase 19 scout) --------------------------------------------------


def discovery_inputs(ctx: ScanToolContext, *, market: str, date: date) -> dict:
    """scout 입력(`_discovery_inputs`, 파이썬이 세션 앞에서 만든다). 없으면 ok=false → 발굴 생략."""
    if market not in ("KR", "US"):
        return {"ok": False, "error": f"market 은 KR 또는 US: {market!r}"}
    d = market_dir(date, market, ctx.state)
    data = (read_checkpoint(d, "_discovery_inputs") or {}).get("data") or {}
    if not data:
        return {"ok": False, "error": "_discovery_inputs 없음 (발굴 단계 생략)"}
    return {"ok": True, "data": _shrink(data, BUNDLE_LIMIT_BYTES)}


def submit_discovery(
    ctx: ScanToolContext,
    *,
    market: str,
    date: date,
    payload: dict,
    agent: str = "scout",
    call_meta: dict | None = None,
) -> dict:
    """Discovered 검증 → 파이썬이 코드·거래소·제외 확정(`discovery.resolve`) → `_discovery` 저장 →
    정량 후보와 병합한 screener 입력(`candidates`) 반환."""
    if market not in ("KR", "US"):
        return {"ok": False, "error": f"market 은 KR 또는 US: {market!r}"}
    try:
        found = schemas.Discovered.model_validate(_unescape(payload))
    except ValidationError as e:
        msgs = "; ".join(
            f"{'.'.join(str(x) for x in err['loc'])}: {err['msg']}" for err in e.errors()
        )
        return {"ok": False, "error": f"스키마 검증 실패(Discovered): {msgs}"}
    d = market_dir(date, market, ctx.state, create=True)
    inputs = (read_checkpoint(d, "_discovery_inputs") or {}).get("data") or {}
    try:
        universe = load_universe()
    except Exception:  # noqa: BLE001 - 설정이 없으면 유니버스 비움(전부 listed 로 본다)
        universe = Universe()
    cache_dir = (ctx.state or paths.state_dir()) / "cache"
    cache_names = names.load_cache_names(cache_dir)
    exclude = set(regime_api.excluded_tickers(ctx.settings, market))
    exclude |= set(inputs.get("held") or [])
    resolved = discovery.resolve(
        found.model_dump(),
        market=market,
        universe=universe,
        cache_names=cache_names,
        market_client=ctx.market_client,
        exclude=exclude,
        event_watch=list(inputs.get("event_watch") or []),
        max_event=int(inputs.get("max_event_picks") or 0),
        max_per_event=int(inputs.get("max_picks_per_event") or 2),
    )
    path = write_checkpoint(d, "_discovery", _wrap(resolved, _call(agent, call_meta)))
    if inputs.get("intraday_round"):  # 장중 발굴(Phase 23)은 회차 사본도 남긴다
        write_checkpoint(
            d,
            f"_discovery_r{int(inputs['intraday_round'])}",
            _wrap(resolved, _call(agent, call_meta)),
        )
    merged = discovery.merge(
        list(inputs.get("quant_candidates") or []),
        resolved,
        max_event=int(inputs.get("max_event_picks") or 0),
    )
    logger.info("발굴 저장 %s: %s", market, discovery.line(resolved))
    return {
        "ok": True,
        "path": str(path),
        "candidates": merged,
        "discovered": [c["ticker"] for c in resolved["candidates"]],
        "unresolved": resolved["unresolved"],
    }
