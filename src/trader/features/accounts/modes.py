"""계좌 모드(원장 account_modes)·LIVE 확인·실효 모드·paused 플래그."""

from __future__ import annotations

import json
import logging
import sqlite3
from typing import Literal, get_args

from trader.features.accounts.settings import Account, Mode
from trader.kernel.ledger.control import get_flag, log_change, set_flag
from trader.kernel.ledger.db import now_iso

logger = logging.getLogger("trader.accounts")

LIVE_CONFIRM = "LIVE"
REAL_FO_OFF_NOTE = "실전 파생 계좌는 주문 없음(파생 레인 범위 밖)"
LIVE_UNCONFIRMED_NOTE = "실전 auto 는 확인 기록(live_confirmed) 없이는 off 로 취급"


class ConfirmationRequired(Exception):
    """실전 계좌를 auto 로 전환하려는데 확인 문자열이 없을 때 발생."""


def sync_accounts(conn: sqlite3.Connection, accounts: list[Account]) -> None:
    """yaml 계좌 목록을 accounts 테이블에 upsert 한다(first_seen_at 은 최초만)."""
    now = now_iso()
    for a in accounts:
        conn.execute(
            "INSERT INTO accounts(id, env, asset, acnt_prdt_cd, markets_json, yaml_mode, "
            "first_seen_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(id) DO UPDATE SET env=excluded.env, asset=excluded.asset, "
            "acnt_prdt_cd=excluded.acnt_prdt_cd, markets_json=excluded.markets_json, "
            "yaml_mode=excluded.yaml_mode, updated_at=excluded.updated_at",
            (
                a.id,
                a.env,
                a.asset,
                a.acnt_prdt_cd,
                json.dumps(list(a.markets)),
                a.mode,
                now,
                now,
            ),
        )
    conn.commit()


def get_account_mode(conn: sqlite3.Connection, account: Account) -> Mode:
    """원장 값이 있으면 그것을, 없으면 yaml 모드를 돌려준다."""
    row = conn.execute(
        "SELECT mode FROM account_modes WHERE account_id = ?", (account.id,)
    ).fetchone()
    return account.mode if row is None else row["mode"]


def set_account_mode(
    conn: sqlite3.Connection,
    account: Account,
    mode: str,
    *,
    confirm: str | None = None,
    channel: str = "cli",
    actor: str | None = None,
) -> None:
    """검증을 전부 마친 뒤 한 문장으로 쓴다. 같은 값이면 updated_at 이 바뀌지 않는다.

    실전 auto 의 LIVE 확인 기록(`live_confirmed_json`)은 confirm·at·channel·actor(누가 줬는지)."""
    if mode not in get_args(Mode):
        raise ValueError(f"알 수 없는 모드: {mode}")
    if account.env == "real" and mode == "auto" and confirm != LIVE_CONFIRM:
        raise ConfirmationRequired(
            f"실전 계좌 '{account.id}' 를 auto 로 바꾸려면 confirm='{LIVE_CONFIRM}' 가 필요합니다."
        )
    old = get_account_mode(conn, account)
    confirmed = (
        json.dumps({"confirm": confirm, "at": now_iso(), "channel": channel, "actor": actor})
        if account.env == "real" and mode == "auto"
        else None
    )
    conn.execute(
        "INSERT INTO account_modes(account_id, mode, live_confirmed_json, updated_at) "
        "VALUES (?, ?, ?, ?) ON CONFLICT(account_id) DO UPDATE SET mode=excluded.mode, "
        "live_confirmed_json=excluded.live_confirmed_json, updated_at=excluded.updated_at "
        "WHERE account_modes.mode IS NOT excluded.mode "
        "OR account_modes.live_confirmed_json IS NOT excluded.live_confirmed_json",
        (account.id, mode, confirmed, now_iso()),
    )
    conn.commit()
    if old != mode:
        log_change(
            conn, channel=channel, actor=actor, key=f"account_mode:{account.id}",
            old=old, new=mode, at=now_iso(),
        )
    logger.info("계좌 %s 모드 → %s", account.id, mode)


def live_confirmed(conn: sqlite3.Connection, account_id: str) -> dict | None:
    row = conn.execute(
        "SELECT live_confirmed_json FROM account_modes WHERE account_id = ?", (account_id,)
    ).fetchone()
    if row is None or row["live_confirmed_json"] is None:
        return None
    return json.loads(row["live_confirmed_json"])


def effective_mode(conn: sqlite3.Connection, account: Account) -> tuple[Mode, str | None]:
    """실전 auto 는 확인 기록이 없으면 off 로 취급한다."""
    mode = get_account_mode(conn, account)
    if account.env == "real" and mode == "auto" and live_confirmed(conn, account.id) is None:
        return "off", LIVE_UNCONFIRMED_NOTE
    return mode, None


def account_mode(conn: sqlite3.Connection, account: Account) -> tuple[Mode, str | None]:
    """주문 경로가 쓰는 최종 모드. 실전 파생 계좌는 항상 off."""
    if account.env == "real" and account.asset == "fo":
        return "off", REAL_FO_OFF_NOTE
    return effective_mode(conn, account)


def mode_source(conn: sqlite3.Connection, account_id: str) -> Literal["control", "yaml"]:
    row = conn.execute("SELECT 1 FROM account_modes WHERE account_id = ?", (account_id,)).fetchone()
    return "yaml" if row is None else "control"


def is_paused(conn: sqlite3.Connection) -> bool:
    return bool(get_flag(conn, "paused", False))


def set_paused(
    conn: sqlite3.Connection, value: bool, *, channel: str = "cli", actor: str | None = None
) -> None:
    old = is_paused(conn)
    set_flag(conn, "paused", bool(value))
    if old != bool(value):
        log_change(
            conn, channel=channel, actor=actor, key="paused",
            old=old, new=bool(value), at=now_iso(),
        )
