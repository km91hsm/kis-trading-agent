"""데몬 잡: 세이브티커 Chrome 확장 수집 감시(장애·복구 텔레그램)·운영일 1회 보관 정리·웹
'다시 시작' 요청(control `saveticker_restart_requested`) 소비·기동 뒤 첫 틱의 확장 복사본
동기화. 수집 자체는 일반 Chrome 의 확장과 Native host 가 하므로 이 잡은 아무것도 띄우지 않는다."""

from __future__ import annotations

import logging
from contextlib import closing
from dataclasses import dataclass
from datetime import date
from typing import Any

from trader.features.saveticker import automation
from trader.features.saveticker.messages import EXTENSION_RELOAD_NOTE, saveticker_health
from trader.features.saveticker.paths import resolve_dir
from trader.features.saveticker.restart import EXTENSION_NOTE, RESTART_FLAG
from trader.features.saveticker.settings import load_cfg
from trader.features.saveticker.store import Store
from trader.kernel.config import KST
from trader.kernel.ledger import control
from trader.kernel.registry.types import JobSpec

logger = logging.getLogger("trader.features.saveticker")

SAVETICKER_WATCH_S = 60


@dataclass
class _State:
    """데몬 프로세스 안의 잡 상태(마지막 정리일·확장 동기화 여부)."""

    pruned: date | None = None
    synced: bool = False


_STATE = _State()


def reset_state() -> None:
    """테스트용: 프로세스 전역 상태를 비운다."""
    global _STATE
    _STATE = _State()


def consume_restart(ctx: Any) -> str | None:
    """웹 '다시 시작' 요청을 소비한다(T11-R2). 요청이 없으면 None. 확장 수집은 Chrome 확장
    팝업에서 재개하므로 안내만 보낸다. 안내 전송이 실패해도 로그·결과로 남기고 플래그는 지운다
    (같은 실패를 매 틱 반복하지 않게 — 사람이 다시 누른다)."""
    with closing(ctx.conn_factory()) as conn:
        req = control.get_flag(conn, RESTART_FLAG)
    if req is None:
        return None
    try:
        ctx.notifier.send(saveticker_health(f"다시 시작 요청 — {EXTENSION_NOTE}"))
        logger.info("세이브티커 다시 시작 요청(%s): 확장 팝업 재개 안내", req.get("actor"))
        result = EXTENSION_NOTE
    except Exception as e:  # noqa: BLE001 — 실패는 로그로 남기고 요청을 닫는다
        logger.exception("세이브티커 다시 시작 요청 처리 실패")
        result = f"실패: {type(e).__name__}"
    with closing(ctx.conn_factory()) as conn:
        control.delete_flag(conn, RESTART_FLAG)
    return result


def sync_extension(ctx: Any) -> dict | str | None:
    """프로세스당 1회(앱 기동 뒤 첫 틱): 설치된 확장 복사본·Native host 등록을 지금 앱에 맞춘다
    (`bridge_install.sync`). 확장 JS 가 바뀌었으면 Chrome 새로고침 안내를 한 번 보낸다. 실패는
    로그로 남기고 수집 감시는 계속한다. 바뀐 게 없으면 None."""
    if _STATE.synced:
        return None
    _STATE.synced = True
    from trader.features.saveticker import bridge_install

    try:
        result = bridge_install.sync()
    except Exception as e:  # noqa: BLE001 — 동기화 실패가 수집 감시를 막지 않게
        logger.exception("세이브티커 확장 동기화 실패")
        return f"실패: {type(e).__name__}"
    if result.get("skipped") == "busy":  # 웹 「설치하기」가 진행 중 — 다음 틱에 다시
        _STATE.synced = False
        return None
    if not (result.get("extension_updated") or result.get("host_updated")):
        return None
    logger.info("세이브티커 확장 동기화: %s", result)
    if result.get("extension_updated"):
        try:
            ctx.notifier.send(saveticker_health(EXTENSION_RELOAD_NOTE))
        except Exception:  # noqa: BLE001 — 안내 실패는 로그만
            logger.exception("세이브티커 확장 새로고침 안내 전송 실패")
    return result


def watch(ctx: Any, job: Any) -> dict:
    """세이브티커 확장 수집 감시. 웹 '다시 시작' 요청을 소비하고, 확장(Native host)이 등록돼
    있으면 수집기 감시(`automation.monitor`)가 판정한 장애·복구를 텔레그램으로 한 번씩 보내고,
    보관 기간 정리는 운영일 1회. 가벼운 잡(heavy 밖)."""
    cfg = load_cfg(registry=ctx.registry)
    home = resolve_dir(cfg.dir)
    if not cfg.collect or home is None:
        return {"skipped": "꺼짐"}
    if ctx.stop_event.is_set():
        return {"skipped": "종료 중"}
    out: dict[str, Any] = {}
    synced = sync_extension(ctx)
    if synced is not None:
        out["extension_sync"] = synced
    restart = consume_restart(ctx)
    if restart is not None:
        out["restart"] = restart
    if automation.registered(home):
        out["service"] = "일반 Chrome 확장 수집 모드"
        health = automation.monitor(
            home, send=lambda text: ctx.notifier.send(saveticker_health(text))
        )
        out["health"] = health.get("health")
    else:
        out["service"] = "확장 미등록"
    today = ctx.now().astimezone(KST).date()
    if _STATE.pruned != today and (home / "data").is_dir():
        _STATE.pruned = today
        out["pruned"] = Store(home / "data").prune(today=today, keep_days=cfg.retention_days)
    return out


JOB = JobSpec(
    id="saveticker",
    kind="saveticker",
    trigger="interval",
    per_market=False,
    schedule_key=None,
    interval_s=SAVETICKER_WATCH_S,
    enabled=None,
    gate="none",
    heavy=False,
    misfire="default",
    catch_up="none",
    start_soon_s=0,
    body=watch,
)
