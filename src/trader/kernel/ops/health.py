"""운영 점검 러너 — 커널 내장 영역(jobs·llm·orders)과 기능 `health_areas` 를 순회해 단계를 판정한다.

단계 `ok < watch < warning < critical`, 머리 단계는 항목 중 최악. 판정은 읽기 전용이고, 결과
보관(`store`)·격상 알림(`escalate`)은 데몬 `ops_check` 잡이 부른다. 영역 하나가 예외를 던지면 그
영역만 `"{key}.error"` warning 1건으로 바꾸고 나머지는 그대로 계산한다."""

from __future__ import annotations

import logging
import sqlite3
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from trader.kernel.config.time import KST
from trader.kernel.ledger.control import get_flag, set_flag
from trader.kernel.notify import messages
from trader.kernel.registry.types import Feature

logger = logging.getLogger("trader.ops")

LEVELS = ("ok", "watch", "warning", "critical")
LABEL = {"ok": "정상", "watch": "관심", "warning": "주의", "critical": "경보"}
# 웹 `/system/health` 단계. watch(info)는 사유 목록에만 들어가고 머리 단계에는 반영하지 않는다.
WEB_LEVEL = {"ok": "ok", "watch": "info", "warning": "warn", "critical": "fail"}
_WEB_HEAD = {"warning": "warn", "critical": "fail"}

LEVEL_FLAG = "ops_level"
RESULT_FLAG = "ops_result"  # 마지막 점검 결과 전체(웹·텔레그램·STATUS 공용)
ALERT_FROM = "warning"  # 이 단계 이상 항목은 새로 생기면 단계가 같아도 알린다


@dataclass
class Item:
    key: str  # 영역.세부 (예: orders.unknown)
    level: str
    text: str
    link: str | None = None  # 웹 경로 (조치 필요 행이 가리킨다)
    count: int | None = None
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class OpsContext:
    """영역 check 함수가 받는 읽기 전용 문맥. ports 는 부트가 조립한 포트(`accounts` 등)."""

    conn: sqlite3.Connection
    settings: Any
    state: Path
    now: datetime
    ports: Mapping[str, Any]
    jobs: Sequence[Any]
    features: Sequence[Feature]


def worst(levels: Iterable[str]) -> str:
    return max(levels, key=LEVELS.index, default="ok")


def _run_area(area: Any, ctx: OpsContext) -> list[Item]:
    try:
        got = list(area.check(ctx) or [])
    except Exception as e:  # noqa: BLE001 - 영역 하나가 점검 전체를 막지 않는다
        logger.warning("점검 영역 %s 실패", area.key, exc_info=True)
        return [Item(f"{area.key}.error", "warning", f"점검 실패: {type(e).__name__}: {e}")]
    prefix = f"{area.key}."
    for it in got:
        if not it.key.startswith(prefix):
            logger.warning("점검 영역 %s 의 항목 키 %s 에 접두를 붙입니다", area.key, it.key)
            it.key = prefix + it.key
    return got


def check(ctx: OpsContext) -> dict:
    """{level, label, at, areas:{영역: level}, items:[비-ok Item], actions:[link 있는 비-ok]}.
    영역 순서는 커널 내장 영역 뒤 `ctx.features` 순서의 `health_areas`."""
    from trader.kernel.ops.areas import KERNEL_AREAS  # areas 가 Item 을 import 한다(순환 회피)

    items: list[Item] = []
    areas: dict[str, str] = {}
    for area in (*KERNEL_AREAS, *(a for f in ctx.features for a in f.health_areas)):
        got = _run_area(area, ctx)
        areas[area.key] = worst([areas.get(area.key, "ok"), *(i.level for i in got)])
        items += got
    level = worst(i.level for i in items)
    return {
        "level": level,
        "label": LABEL[level],
        "at": ctx.now.isoformat(),
        "areas": areas,
        "items": [asdict(i) for i in items if i.level != "ok"],
        "actions": [asdict(i) for i in items if i.link and i.level != "ok"],
    }


# --- 결과 보관·격상 (데몬) ----------------------------------------------------------------


def latest(conn: sqlite3.Connection) -> dict | None:
    """데몬이 마지막으로 적은 점검 결과(control `ops_result`). 없으면 None."""
    value = get_flag(conn, RESULT_FLAG)
    return value if isinstance(value, dict) else None


def store(conn: sqlite3.Connection, result: dict) -> None:
    set_flag(conn, RESULT_FLAG, result)


def _alert_keys(result: dict) -> set[str]:
    return {
        str(i["key"])
        for i in result["items"]
        if i.get("key") and LEVELS.index(i.get("level", "ok")) >= LEVELS.index(ALERT_FROM)
    }


def escalate(conn: sqlite3.Connection, result: dict) -> str | None:
    """단계가 나빠졌거나, 같은 단계라도 경고 이상 **새 항목**이 생겼으면 알림 문구를 돌려준다.
    현재 단계와 경고 항목 키를 control `ops_level` 에 적는다(좋아져도 적되 알림은 없다)."""
    prev = get_flag(conn, LEVEL_FLAG) or {}
    prev_level = str(prev.get("level") or "ok")
    prev_keys = set(prev.get("keys") or [])
    keys = _alert_keys(result)
    if result["level"] != prev.get("level") or not prev.get("at") or keys != prev_keys:
        # at = 이 단계가 된 시각. 단계·경고 키가 같으면 다시 쓰지 않는다
        record = {"level": result["level"], "at": result["at"], "keys": sorted(keys)}
        set_flag(conn, LEVEL_FLAG, record)
    worse = LEVELS.index(result["level"]) > LEVELS.index(prev_level)
    new = keys - prev_keys
    if not worse and not new:
        return None
    items = result["items"] if worse else [i for i in result["items"] if i["key"] in new]
    heads = "; ".join(i["text"] for i in items[:4])
    title = f"{LABEL[prev_level]} → {result['label']}" if worse else f"새 경고 {len(new)}건"
    return messages.event("점검 격상", title, heads)


def headline(result: dict | None) -> str:
    if not result:
        return "점검 기록 없음"
    at = datetime.fromisoformat(result["at"]).astimezone(KST).strftime("%H:%M")
    if result["level"] == "ok":
        return f"점검 정상 · {at}"
    return f"점검 {result['label']} · {len(result['items'])}건 · {at}"


# --- 웹 변환 ----------------------------------------------------------------------------


def to_web(result: dict, *, version: str, features: Sequence[str]) -> dict:
    """`/system/health` 응답 모양 `{level: ok|warn|fail, reasons, checked_at, version, features}`.
    ok 항목은 빼고, watch 는 info 사유로만 남긴다(머리 단계 미반영)."""
    reasons = [
        {
            "area": str(i["key"]).split(".")[0],
            "level": WEB_LEVEL[i["level"]],
            "text": i["text"],
            "key": i["key"],
            "link": i.get("link"),
        }
        for i in result["items"]
        if i["level"] != "ok"
    ]
    return {
        "level": _WEB_HEAD.get(result["level"], "ok"),
        "reasons": reasons,
        "checked_at": result["at"],
        "version": version,
        "features": list(features),
    }
