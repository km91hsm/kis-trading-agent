"""spot 승인 결정: 웹·텔레그램이 같은 길(`decide`)로 승인 요청을 **선점**한다.

선점은 `approvals.decided_by/decided_channel` 조건부 UPDATE 한 번이라, 두 채널이 동시에 눌러도
결정은 하나다(두 번째는 409 상당). 실전 계좌 승인은 `confirm == account_id` 가 아니면 선점하지
않는다(fail-closed — 계좌를 모르면 실전으로 본다). 거부는 바로 `rejected` 로 닫고, 승인은
선점까지만 — 집행은 호출자 몫이다(웹은 `approve_execute` 큐 요청, 데몬 안의 텔레그램은
`execute`). 코어가 승인을 pending 으로 남기면(일시정지·모드·브로커 없음 등) 선점을 풀어 다시
결정할 수 있게 한다. 모든 결정은 `control_log`(`approval:<id>`)에 남는다."""

from __future__ import annotations

import logging
import sqlite3
from collections.abc import Iterable
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Literal

from trader.features.spot import executor
from trader.features.spot.positions_view import CURRENCY
from trader.kernel.execution import ledger as xledger
from trader.kernel.ledger import control

logger = logging.getLogger("trader.spot")

Action = Literal["approve", "reject"]

NO_REASON = "근거 없음"
SUMMARY_MAX = 280
SUMMARY_LINES = 4
LIST_LIMIT = 200
UNKNOWN_MESSAGE = (
    "주문 응답을 받지 못했어요. 접수됐을 수 있으니 다시 누르지 말고 KIS 앱에서 확인하세요. "
    "체결 동기화가 상태를 확정해요."
)


def _fail(approval_id: int, status: str, error: str) -> dict:
    return {"ok": False, "approval_id": approval_id, "status": status, "error": error}


def env_of(accounts: Iterable[Any], account_id: str) -> str | None:
    """계좌 env(paper·real). 모르는 계좌면 None — 확인 판정은 실전으로 본다."""
    for a in accounts:
        if getattr(a, "id", None) == account_id:
            return getattr(a, "env", None)
    return None


def summary_of(conn: sqlite3.Connection, approval: dict) -> str:
    """결정 `data` 의 요약(없으면 근거) 문단을 4줄·280자 이내로. 결정이 없으면 "근거 없음"."""
    did = approval.get("decision_id")
    if not did:
        return NO_REASON
    try:
        dec = xledger.get_decision(conn, int(did), hydrate=True)
    except (ValueError, TypeError) as e:  # 깨진 data_json — 목록 전체를 막지 않는다
        logger.warning("결정 #%s 요약 읽기 실패: %s", did, type(e).__name__)
        return NO_REASON
    data = (dec or {}).get("data") or {}
    text = str(data.get("summary") or data.get("rationale") or "").strip()
    lines = [x.strip() for x in text.splitlines() if x.strip()][:SUMMARY_LINES]
    out = "\n".join(lines)
    if len(out) > SUMMARY_MAX:
        out = out[: SUMMARY_MAX - 1] + "…"
    return out or NO_REASON


def _amount(order: dict) -> str | None:
    price = order.get("price") or order.get("quote_price")
    try:
        return str(Decimal(str(price)) * int(order["qty"])) if price else None
    except (InvalidOperation, KeyError, TypeError, ValueError):
        return None


def _market_of(order: dict, accounts: Iterable[Any], account_id: str) -> str | None:
    """주문 시장. 주문에 없으면 계좌가 시장 하나뿐일 때 그것, 아니면 None."""
    if order.get("market"):
        return str(order["market"]).upper()
    acct = next((x for x in accounts if getattr(x, "id", None) == account_id), None)
    markets = list(getattr(acct, "markets", None) or [])
    return markets[0] if len(markets) == 1 else None


def row_of(conn: sqlite3.Connection, a: dict, accounts: Iterable[Any]) -> dict:
    """웹 목록 행. 통화는 계좌가 아니라 주문 시장 기준(KR→KRW, US→USD)."""
    o, accounts = a["order"], list(accounts)
    market = _market_of(o, accounts, a["account_id"])
    return {
        "id": a["id"],
        "account_id": a["account_id"],
        "env": env_of(accounts, a["account_id"]),
        "decision": {"asset": "spot", "id": a["decision_id"] or None},
        "ticker": o.get("ticker"),
        "market": market,
        "currency": CURRENCY.get(market) if market else None,
        "side": o.get("side"),
        "qty": o.get("qty"),
        "amount": _amount(o),
        "summary": summary_of(conn, a),
        "expires_at": a["expires_at"],
        "status": a["status"],
        "decided_by": a.get("decided_by"),
        "decided_channel": a.get("decided_channel"),
    }


def list_rows(
    conn: sqlite3.Connection, accounts: Iterable[Any], *, status: Literal["pending", "all"]
) -> list[dict]:
    """pending(오래된 순) 또는 전체 최근 `LIST_LIMIT` 건(최신 순)."""
    accounts = list(accounts)
    if status == "pending":
        rows = xledger.pending_approvals(conn)
    else:
        ids = [
            r["id"]
            for r in conn.execute(
                "SELECT id FROM approvals ORDER BY id DESC LIMIT ?", (LIST_LIMIT,)
            ).fetchall()
        ]
        rows = [a for a in (xledger.get_approval(conn, i) for i in ids) if a is not None]
    return [row_of(conn, a, accounts) for a in rows]


def decide(
    conn: sqlite3.Connection,
    approval_id: int,
    action: Action,
    *,
    channel: str,
    actor: str | None,
    confirm: str | None,
    now: datetime,
    accounts: Iterable[Any],
) -> dict:
    """승인 요청 1건을 결정한다(선점). 반환:

    - 없음·처리됨·이미 결정됨·만료 → `{"ok": False, "status": …, "error"}` (HTTP 409 상당)
    - 실전 승인인데 `confirm != account_id` → `{"ok": False, "status": "confirm_required"}` (400)
    - 선점 경합(0행) → `{"ok": False, "status": "race"}` (409)
    - 거부 → `rejected` 로 닫고 `{"ok": True, "status": "rejected"}`
    - 승인 → `{"ok": True, "status": "accepted"}` — 집행은 호출자가 한다."""
    a = xledger.get_approval(conn, approval_id)
    if a is None:
        return _fail(approval_id, "missing", f"승인 요청 #{approval_id} 없음")
    if a["status"] != "pending":
        return _fail(
            approval_id, a["status"], f"승인 요청 #{approval_id} 은 이미 처리됨: {a['status']}"
        )
    if a.get("decided_channel") or a.get("decided_by"):
        return _fail(
            approval_id, "decided",
            f"승인 요청 #{approval_id} 은 이미 {a.get('decided_channel') or '다른 곳'}에서 "
            "결정됨(집행 대기)",
        )  # fmt: skip
    at = now.isoformat()
    if datetime.fromisoformat(a["expires_at"]) <= now:
        xledger.decide_approval(conn, approval_id, "expired", decided_at=at)
        return _fail(approval_id, "expired", f"승인 요청 #{approval_id} 은 만료됨 (30분 초과)")
    account_id = a["account_id"]
    if action == "approve" and (env_of(accounts, account_id) or "real") != "paper":
        if confirm != account_id:
            return _fail(
                approval_id, "confirm_required",
                f"{account_id} 은(는) 실전 계좌예요 — 승인하려면 계좌 id 로 확인해야 해요",
            )  # fmt: skip
    cur = conn.execute(
        "UPDATE approvals SET decided_by=?, decided_channel=?, decided_at=? "
        "WHERE id=? AND decided_by IS NULL AND status='pending'",
        (actor or channel, channel, at, approval_id),
    )
    if cur.rowcount != 1:
        conn.commit()
        return _fail(approval_id, "race", f"승인 요청 #{approval_id} 은 이미 다른 곳에서 결정됨")
    control.log_change(  # 선점과 같은 트랜잭션으로 커밋된다
        conn, channel=channel, actor=actor, key=f"approval:{approval_id}", old="pending",
        new=action, at=at,
    )  # fmt: skip
    if action == "reject":
        if not xledger.decide_approval(conn, approval_id, "rejected", decided_at=at):
            return _fail(approval_id, "race", f"승인 요청 #{approval_id} 은 이미 처리됨")
        return {"ok": True, "approval_id": approval_id, "status": "rejected"}
    return {"ok": True, "approval_id": approval_id, "status": "accepted"}


def release(conn: sqlite3.Connection, approval_id: int) -> bool:
    """아직 pending 인 승인의 선점을 푼다(집행이 pending 으로 끝났을 때). 풀었으면 True."""
    cur = conn.execute(
        "UPDATE approvals SET decided_by=NULL, decided_channel=NULL, decided_at=NULL "
        "WHERE id=? AND status='pending'",
        (approval_id,),
    )
    conn.commit()
    return cur.rowcount == 1


def execute(ectx: executor.ExecContext, approval_id: int) -> dict:
    """선점된 승인을 `executor.approve` 로 집행한다(데몬 안 — 큐 본문·텔레그램). 이미 처리된
    승인은 코어가 거부하므로 두 번 불러도 주문은 한 번이다. 코어가 pending 으로 남기거나 예외가
    나면 선점을 푼다(pending 일 때만 — 주문이 나간 승인은 approved·failed 라 그대로).

    결과에 화면용 꼬리를 붙인다: 응답 없음(unknown)은 `unknown`·`message`(실패 아님), 브로커
    거부는 `error`."""
    try:
        r = executor.approve(ectx, approval_id)
    except BaseException:
        release(ectx.conn, approval_id)
        raise
    if r.get("status") == "pending":
        release(ectx.conn, approval_id)
    order = r.get("order")
    if not r.get("ok") and isinstance(order, dict):
        if order.get("status") == "unknown":
            r["unknown"] = True
            r["message"] = UNKNOWN_MESSAGE
        elif not r.get("error"):
            r["error"] = f"브로커 거부: {order.get('message') or '알 수 없음'}"
    return r
