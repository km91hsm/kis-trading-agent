"""데몬 잡: 세이브티커 수집 서비스 감독 + 웹 '다시 시작' 요청
(control `saveticker_restart_requested`) 소비."""

from __future__ import annotations

import logging
import os
from contextlib import closing
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

from trader.features.saveticker import automation
from trader.features.saveticker.messages import saveticker_health
from trader.features.saveticker.paths import credentials_path, resolve_dir
from trader.features.saveticker.routes import EXTENSION_NOTE, RESTART_FLAG
from trader.features.saveticker.settings import load_cfg
from trader.features.saveticker.store import Store
from trader.kernel.config import KST
from trader.kernel.ledger import control
from trader.kernel.platform import bundle, procs
from trader.kernel.platform.paths import project_root
from trader.kernel.registry.types import JobSpec

logger = logging.getLogger("trader.features.saveticker")

SAVETICKER_WATCH_S = 60


@dataclass
class _State:
    """데몬 프로세스 안의 수집 서비스 상태(띄운 프로세스·폴더·마지막 정리일)."""

    proc: Any = None
    home: Path | None = None
    pruned: date | None = None


_STATE = _State()


def reset_state() -> None:
    """테스트용: 프로세스 전역 상태를 비운다."""
    global _STATE
    _STATE = _State()


def consume_restart(ctx: Any, home: Path) -> str | None:
    """웹 '다시 시작' 요청을 소비한다(T11-R2). 요청이 없으면 None. 확장 백엔드는 Chrome 확장
    팝업에서 재개하므로 정지 기록을 지우지 않고 안내만 보내고(옛 409 대신), 그 외는 옛 동작대로
    `automation.clear_blocks`(작업 쉬기·서비스 정지 기록 지움 → 같은 틱에 다시 띄움). 어느 쪽이든
    플래그는 지운다."""
    with closing(ctx.conn_factory()) as conn:
        req = control.get_flag(conn, RESTART_FLAG)
        if req is None:
            return None
        control.delete_flag(conn, RESTART_FLAG)
    bridge = automation.read(home / "runtime" / "bridge-config.json") or {}
    if bridge.get("backend") == "extension":
        ctx.notifier.send(saveticker_health(f"다시 시작 요청 — {EXTENSION_NOTE}"))
        logger.info("세이브티커 다시 시작 요청: 확장 모드라 팝업 재개 안내만")
        return EXTENSION_NOTE
    automation.clear_blocks(home)
    logger.info("세이브티커 다시 시작 요청 소비: 정지 기록 지움 (%s)", req.get("actor"))
    return "정지 기록 지움"


def watch(ctx: Any, job: Any) -> dict:
    """세이브티커 수집 서비스 감독(앱·데몬이 켜져 있는 동안 수집). 계정 파일이 있고 다른 서비스
    (예: launchd)가 같은 폴더를 돌리지 않으면 `trader saveticker service` 를 분리 실행하고, 죽으면
    다시 띄운다(인증 실패·재기동 한도는 `automation.should_start` 가 막는다). 장애·복구는 수집기
    감시(`automation.monitor`)가 판정해 텔레그램으로 한 번씩, 보관 기간 정리는 운영일 1회.
    웹 '다시 시작' 요청은 띄우기 판정 전에 소비한다(`consume_restart`).
    가벼운 잡(heavy 밖) — 수집은 별도 프로세스라 데몬 스레드를 붙잡지 않는다."""
    cfg = load_cfg(registry=ctx.registry)
    home = resolve_dir(cfg.dir)
    if not cfg.collect or home is None:
        return {"skipped": "꺼짐"}
    if ctx.stop_event.is_set():
        return {"skipped": "종료 중"}
    st = _STATE
    out: dict[str, Any] = {}
    restart = consume_restart(ctx, home)
    if restart is not None:
        out["restart"] = restart
    proc = st.proc
    if proc is not None and proc.poll() is None:
        out["service"] = "실행 중"
    else:
        ok, reason = automation.should_start(home, credentials_path(), now=ctx.now())
        if ok:
            automation.kill_leftovers(home)  # 죽은 서비스가 남긴 작업이 수집 잠금을 쥐고 있지 않게
            env = {
                **os.environ,
                "TRADER_HOME": str(project_root()),
                "PYTHONIOENCODING": "utf-8",
            }
            st.proc = procs.spawn_detached(
                [*bundle.cli_argv(), "saveticker", "service"],
                log=home / "runtime" / "daemon-service.log",
                env=env,
            )
            st.home = home
            out["service"] = f"시작 pid {st.proc.pid}"
            logger.info("세이브티커 수집 서비스 시작 pid %s", st.proc.pid)
        else:
            out["service"] = reason
    if out["service"] != "계정 파일 없음":
        health = automation.monitor(
            home, send=lambda text: ctx.notifier.send(saveticker_health(text))
        )
        out["health"] = health.get("health")
    today = ctx.now().astimezone(KST).date()
    if st.pruned != today and (home / "data").is_dir():
        st.pruned = today
        out["pruned"] = Store(home / "data").prune(today=today, keep_days=cfg.retention_days)
    return out


def stop(ctx: Any) -> None:
    """데몬 종료 때 이 데몬이 띄운 수집 서비스를 정리한다(서비스 → 수집 작업 → 브라우저 순)."""
    st = _STATE
    proc, st.proc = st.proc, None
    if proc is None:
        return
    try:
        procs.stop_tree(proc, grace=automation.STOP_GRACE_S)
        if st.home is not None:  # 강제 종료로 끝났으면 작업·브라우저가 남는다
            automation.kill_leftovers(st.home)
    except Exception:  # noqa: BLE001 — 종료 경로는 막지 않는다
        logger.exception("세이브티커 수집 서비스 정리 실패")


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
    teardown=stop,
)
