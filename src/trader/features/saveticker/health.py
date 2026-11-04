"""saveticker 운영 점검 영역(계정 파일이 있을 때만): 수집 서비스 멈춤 warning, 접근이 막혀 쉬는
작업 watch. 데몬 감시 잡은 정지 상태여도 'ok' 로 끝나 보이지 않던 문제(2026-10-29)."""

from __future__ import annotations

import logging

from trader.features.saveticker import automation, paths
from trader.kernel.ops.types import Item, OpsContext
from trader.kernel.registry.types import HealthArea

logger = logging.getLogger("trader.features.saveticker")
LINK = "/system#saveticker"


def check(ctx: OpsContext) -> list[Item]:
    try:
        if not paths.credentials_path().is_file():
            return []
        home = paths.home()
        s = automation.status(home)
        paused = automation.active_skip_lanes(home, now=ctx.now)
    except Exception as e:  # noqa: BLE001 — 점검은 읽기 전용, 실패가 다른 영역을 막지 않는다
        logger.debug("세이브티커 점검 실패: %s", e)
        return []
    out: list[Item] = []
    if s["blocked"]:
        text = f"세이브티커 수집 멈춤 — {s['reason_text']}"
        out.append(Item("saveticker.stopped", "warning", text, link=LINK))
    if paused:
        text = f"세이브티커 {', '.join(paused)} 작업 쉬는 중(접근 거부, 6시간 뒤 다시 시도)"
        out.append(Item("saveticker.lane_paused", "watch", text, link=LINK))
    return out


AREA = HealthArea("saveticker", check)
