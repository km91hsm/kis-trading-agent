"""단타 계좌별 스위치 — control 플래그 `scalp_enabled:<계좌 id>` 와 실효 판정.

저장값: 플래그가 있으면 그 값(dict 면 `enabled`), 없으면 모의 계좌는 yaml `scalp.enabled`,
실전 계좌는 꺼짐. 실효: 저장값 켜짐 ∧ 일시정지 아님 ∧ (실전이면 계좌 모드 auto — LIVE 확인이
없으면 포트 policy 가 이미 off 를 돌려준다). accounts 는 포트로만 만난다."""

from __future__ import annotations

import sqlite3
from typing import Any, Literal

from trader.kernel.ledger.control import get_flag

FLAG_PREFIX = "scalp_enabled:"

Source = Literal["control", "yaml", "default"]


def flag_key(account_id: str) -> str:
    return f"{FLAG_PREFIX}{account_id}"


def stored(conn: sqlite3.Connection, settings: Any, account: Any) -> tuple[bool, Source]:
    """저장된 스위치 값과 출처(실효 판정 전)."""
    raw = get_flag(conn, flag_key(account.id), None)
    if raw is not None:
        if isinstance(raw, dict):
            raw = raw.get("enabled")
        return bool(raw), "control"
    if account.env == "paper":
        return bool(settings.scalp.enabled), "yaml"
    return False, "default"


def account_enabled(
    conn: sqlite3.Connection, settings: Any, account: Any, port: Any
) -> tuple[bool, str | None]:
    """이 계좌에 단타 다리를 만들어도 되는가. 거짓이면 사유."""
    if not stored(conn, settings, account)[0]:
        return False, "꺼짐"
    if port.paused(conn):
        return False, "전체 일시정지"
    if account.env != "paper":
        mode, note = port.policy(conn, account)
        if mode != "auto":
            return False, note or f"실전: 계좌 모드가 자동이 아님({mode})"
    return True, None


def any_enabled(
    conn: sqlite3.Connection, settings: Any, port: Any, market: str | None = None
) -> bool:
    """그 시장을 가진 계좌 중 스위치(저장값)가 켜진 계좌가 있는가."""
    return any(
        stored(conn, settings, a)[0]
        for a in port.accounts()
        if market is None or market in a.markets
    )
