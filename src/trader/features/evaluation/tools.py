"""evaluation 세션 도구: `trader tool submit-reflection`.

시장 단위 체크포인트 `_reflect` 를 `state/runs/<날짜>/<시장>/` 에 누적한다. 함수는 예외 대신
{"ok": bool, ...} dict 를 돌려준다."""

from __future__ import annotations

import logging
from datetime import UTC, date, datetime
from typing import Any

from pydantic import ValidationError

from trader.features.evaluation import schemas
from trader.features.evaluation.memory import store
from trader.kernel.lane.types import LaneContext
from trader.kernel.platform import paths
from trader.kernel.session import tooling
from trader.kernel.session.run_store import checkpoint_payload as _wrap
from trader.kernel.session.run_store import market_dir, read_checkpoint, write_checkpoint

logger = logging.getLogger("trader.memory")


def tool_ctx(b: Any) -> LaneContext:
    """부트 결과(`ports=True`)로 LaneContext 를 만든다."""
    return LaneContext(
        settings=b.settings,
        conn=b.conn,
        state=paths.state_dir(),
        now=lambda: datetime.now(UTC),
        dry_run=True,
        notifier=None,
        ports=b.ports,
        ext={"registry": getattr(b, "registry", None)},
    )


def submit_reflection(
    ctx: LaneContext,
    *,
    market: str,
    date: date,
    payload: list | dict,
    agent: str = "reflector",
    call_meta: dict | None = None,
) -> dict:
    """Reflection 배열을 검증해 메모리에 저장하고 runs/<date>/<market>/_reflect.json 에 덧붙인다."""
    if ctx.conn is None:
        return {"ok": False, "error": "원장 연결 없음"}
    if market not in ("KR", "US"):
        return {"ok": False, "error": f"market 은 KR 또는 US: {market!r}"}
    items = payload if isinstance(payload, list) else [payload]
    parsed: list[schemas.Reflection] = []
    for i, item in enumerate(items):
        try:
            parsed.append(schemas.Reflection.model_validate(tooling.unescape(item)))
        except ValidationError as e:
            msgs = "; ".join(
                f"{'.'.join(str(x) for x in err['loc'])}: {err['msg']}" for err in e.errors()
            )
            return {"ok": False, "error": f"스키마 검증 실패(Reflection[{i}]): {msgs}"}
    for r in parsed:
        if store.get_entry(ctx.conn, r.entry_id) is None:
            return {"ok": False, "error": f"메모리 엔트리 없음: {r.entry_id}"}
    for r in parsed:
        store.set_reflection(ctx.conn, r.entry_id, r.model_dump())
    d = market_dir(date, market, ctx.state, create=True)
    meta = tooling.call_meta(agent, call_meta)
    existing = read_checkpoint(d, "_reflect") or _wrap([], meta)
    data = list(existing.get("data") or []) + [r.model_dump() for r in parsed]
    write_checkpoint(d, "_reflect", _wrap(data, meta))
    logger.info("반성 저장 %s: %d건", market, len(parsed))
    return {"ok": True, "saved": len(parsed), "entry_ids": [r.entry_id for r in parsed]}
