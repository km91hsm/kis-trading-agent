"""파생 계좌별 스위치 — control 플래그 `fo_enabled:<계좌 id>`(단타 `scalp_enabled:<계좌>` 모양).

저장값: 플래그가 bool(dict 면 `enabled`)이면 그 값, 없으면 모의 계좌는 yaml `fo.enabled`. 실전 파생
계좌는 항상 꺼짐(실전 파생은 off 고정 — 저장값이 있어도 무시). `enabled` 는 신규 진입만 막고
보유분 감시·축소는 계속한다. 게이트·회수 사유·아침 세션·웹 상태가 이 값을 본다."""

from __future__ import annotations

import sqlite3
from typing import Any, Literal

from trader.kernel.ledger.control import delete_flag, get_flag, log_change, set_flag

FLAG_PREFIX = "fo_enabled:"
NAMESPACE = "fo_enabled"

Source = Literal["control", "yaml", "default"]


def flag_key(account_id: str) -> str:
    return f"{FLAG_PREFIX}{account_id}"


def stored(conn: sqlite3.Connection | None, settings: Any, account: Any) -> tuple[bool, Source]:
    """저장된 스위치 값과 출처."""
    if account.env != "paper":
        return False, "default"
    raw = get_flag(conn, flag_key(account.id), None) if conn is not None else None
    if raw is not None:
        if isinstance(raw, dict):
            raw = raw.get("enabled")
        return raw is True, "control"  # "yes"·1 같은 비불리언 값이 우연히 켜지지 않게
    return bool(settings.fo.enabled), "yaml"


def any_enabled(conn: sqlite3.Connection | None, settings: Any, accounts: list[Any]) -> bool:
    return any(stored(conn, settings, a)[0] for a in accounts)


def set_switch(
    conn: sqlite3.Connection, settings: Any, account: Any, value: bool | None, *,
    channel: str, actor: str | None, at: str,
) -> tuple[bool, Source]:  # fmt: skip
    """저장(None 이면 지워 기본값으로) + 실효 저장값이 바뀌면 control 이력. 실전 켜기는 호출자가
    막는다(여기서는 실전이면 저장해도 `stored` 가 꺼짐을 돌려준다)."""
    key = flag_key(account.id)
    old = stored(conn, settings, account)
    if value is None:
        delete_flag(conn, key)
    else:
        set_flag(conn, key, value)
    new = stored(conn, settings, account)
    if old[0] != new[0]:  # 출처만 바뀌면 기록 없음
        log_change(conn, channel=channel, actor=actor, key=key, old=old[0], new=new[0], at=at)
    return new
