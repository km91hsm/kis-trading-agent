"""saveticker 운영 점검 영역(Chrome 확장이 등록됐을 때만): 확장 연결 끊김 warning, 사이트
확인·로그인이 필요해 멈춤 warning. 데몬 감시 잡은 정지 상태여도 'ok' 로 끝나 보이지 않던
문제(2026-10-29)."""

from __future__ import annotations

from trader.features.saveticker import automation, paths
from trader.kernel.ops.types import Item, OpsContext
from trader.kernel.registry.types import HealthArea

LINK = "/system/features#saveticker"


def check(ctx: OpsContext) -> list[Item]:
    """확장이 등록되지 않았으면 빈 결과(기능 미사용 — 정상 경로). 상태 파일 읽기 실패는 올려
    러너의 `saveticker.error` 격리에 맡긴다(M-4)."""
    home = paths.home()
    if not automation.registered(home):
        return []
    s = automation.status(home)
    out: list[Item] = []
    if s["health"] == "waiting_chrome":
        out.append(Item("saveticker.disconnected", "warning",
                        "세이브티커 확장 연결 끊김 — Chrome 수집 상태를 확인하세요", link=LINK))
    if s["blocked"] and (s.get("diagnostic") or {}).get("retrying"):
        # 지금 차단 이후의 재시도 대기 진단 — 확장이 스스로 다시 시도하므로 관심(info) 수준.
        out.append(Item("saveticker.retrying", "watch",
                        "세이브티커 복구 재시도 중 — 자동으로 다시 시도합니다", link=LINK))
    elif s["blocked"]:
        text = f"세이브티커 수집 멈춤 — {s['reason_text']}"
        out.append(Item("saveticker.stopped", "warning", text, link=LINK))
    return out


AREA = HealthArea("saveticker", check)
