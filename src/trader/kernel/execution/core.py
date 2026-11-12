"""주문 실행 코어: 모든 주문이 지나는 한 곳(옛 `orchestrator/executor.py` 의 `_place`·승인 처리).

- `transmit`: 실행 키에 보낸 시각을 남기고(`mark_sent`) 브로커로 보낸 뒤 상태를 정해 원장에 적고,
  키를 주문 행에 묶고(접수·unknown) 또는 놓고(거부·실패), 체크포인트·알림을 남긴다.
- `request_approval`·`approve`·`reject`·`expire_approvals`: approve 모드의 사람 승인 관문.
  `approve` 는 기능이 아는 판단(계좌 정책·결정 방향·재판정·의견)을 콜백으로 받는다.

커널 모듈이라 `trader.features.*` 를 import 하지 않는다.
알림·로그에는 계좌 **id** 만 쓴다(계좌번호 금지).
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

from trader.kernel.broker.errors import UNKNOWN_RESPONSE_CODE, KisApiError
from trader.kernel.broker.models import OrderResult
from trader.kernel.execution.idem import IdemStore
from trader.kernel.execution.ledger import (
    create_approval,
    decide_approval,
    expire_pending,
    get_approval,
    insert_order,
    set_order_status,
)
from trader.kernel.execution.types import (
    APPROVAL_TTL,
    DEAD_STATUSES,
    ExecPolicy,
    OrderBookkeepingError,
    OrderIntent,
)
from trader.kernel.notify import messages
from trader.kernel.notify.telegram import Message, Notifier
from trader.kernel.notify.telegram import send as send_notice
from trader.kernel.session.run_store import checkpoint_payload, write_checkpoint

logger = logging.getLogger("trader.execution")

__all__ = [
    "ExecDeps",
    "KeepPending",
    "OrderIntent",
    "approval_fingerprint",
    "approve",
    "cancel",
    "expire_approvals",
    "reject",
    "request_approval",
    "transmit",
]

NAME_KEY = "name"  # 승인 order 의 표시용 종목명(OrderRequest 에는 없다, 지문에서도 뺀다)

# 주문 체크포인트 접두 → 알림 제목 꼬리 ('[주문 · 재평가]'). `order_stage` 끝의 라운드(`_r2`)·
# 단타 다리(`_entry`·`_exit`) 꼬리를 벗긴 이름으로 찾는다(옛 저장소는 '_r' 접두 매치라
# 53_order_rebalance·57_order_scalp_* 는 꼬리가 비었다).
_STAGE_KIND = {
    "51_order": "재평가",
    "52_order_reprice": "재호가",
    "53_order_rebalance": "리밸런스",
    "55_order_sl": "손절",
    "56_order_hedge": "헤지",
    "57_order_scalp": "단타",
}


def _kind(order_stage: str) -> str:
    base = re.sub(r"_(r\d+|entry|exit)$", "", order_stage)
    return _STAGE_KIND.get(base, "")


def approval_fingerprint(account_id: str, decision_id: int | None, order: dict) -> str:
    """승인 지문: 계좌·결정·주문 내용(표시용 이름 제외)의 정렬 키 JSON sha256.
    요청 때 저장하고 실행 직전 다시 계산해 다르면(원장 행 변조·버그) 주문하지 않는다."""
    body = {k: v for k, v in order.items() if k != NAME_KEY}
    raw = json.dumps(
        {"account_id": account_id, "decision_id": decision_id or 0, "order": body},
        sort_keys=True,
        ensure_ascii=False,
        default=str,
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class KeepPending:
    """recheck 가 '지금은 판단 불가, 승인은 pending 유지' 를 알리는 반환값."""

    reason: str


@dataclass
class ExecDeps:
    """코어가 쓰는 의존성. `broker_of(account_id)` 는 브로커가 없으면 None(앱키 미설정),
    `label(ticker, name)` 은 알림에 보일 종목 표기."""

    conn: Any
    notifier: Notifier
    now: Callable[[], datetime]
    idem: IdemStore
    broker_of: Callable[[str], Any | None]
    label: Callable[[str, str | None], str] = lambda ticker, name: ticker


def notify_quietly(notifier: Notifier, text: str) -> None:
    """알림 실패가 주문 흐름을 끊지 않도록 예외를 경고로 바꾼다(코어·레인 공용)."""
    try:
        notifier.send(text)
    except Exception as e:  # noqa: BLE001 - 알림 채널 장애는 주문 결과와 무관
        logger.warning("알림 전송 실패: %s", type(e).__name__)


# --- 전송 ---------------------------------------------------------------------------------


def cancel(
    deps: ExecDeps,
    *,
    order_id: int,
    order_no: str,
    market: str,
    broker: Any,
    dry_run: bool,
    reason: str,
    exchange: str | None = None,
) -> dict:
    """미체결 주문 1건을 취소한다. 브로커 취소 1회, 접수(accepted)되면 cancelled 로 바꾼다.

    US 취소에는 org_no·ticker·qty 가 필요해 주문 행에서 읽어 넘긴다(exchange 는 호출자가 준다).
    qty 는 잔량 기준(주문 수량 − 원장 `fills` 합계)이다 — 잔량이 0 이면(전량 체결) 브로커를 부르지
    않고 `ok=False`. 이미 끝난 주문(`DEAD_STATUSES`)도 브로커를 부르지 않고 `ok=False`.
    dry_run 은 브로커도 상태도 건드리지 않고 현재 상태만 돌려준다. 브로커가 거부하면 상태를
    두고 `ok=False`, 예외는 상태를 그대로 두고 다시 올린다(취소 여부를 모르는 채 적지 않는다)."""
    row = deps.conn.execute(
        "SELECT org_no, ticker, qty, status FROM orders WHERE id=?", (order_id,)
    ).fetchone()
    if row is None:
        raise ValueError(f"주문 #{order_id} 없음")
    if row["status"] in DEAD_STATUSES:  # 이미 끝난 주문: KIS 에 보낼 것이 없다
        logger.info("주문 #%d 이미 %s — 취소 생략: %s", order_id, row["status"], reason)
        return {
            "ok": False,
            "order_id": order_id,
            "status": row["status"],
            "message": f"이미 끝난 주문({row['status']})",
        }
    filled = deps.conn.execute(
        "SELECT COALESCE(SUM(qty), 0) AS q FROM fills WHERE order_id=?", (order_id,)
    ).fetchone()["q"]
    remaining = max(int(row["qty"]) - int(filled), 0)
    if remaining == 0:  # 전량 체결: 취소할 잔량이 없다
        logger.info("주문 #%d 잔량 없음 — 취소 생략: %s", order_id, reason)
        return {
            "ok": False,
            "order_id": order_id,
            "status": row["status"],
            "message": "남은 수량 없음(전량 체결)",
        }
    if dry_run:
        logger.info("취소 건너뜀(dry_run) 주문 #%d: %s", order_id, reason)
        return {"ok": True, "dry_run": True, "order_id": order_id, "status": row["status"]}
    try:
        res = broker.cancel_order(
            order_no,
            market=market,
            org_no=row["org_no"],
            ticker=row["ticker"],
            exchange=exchange,
            qty=remaining,
        )
    except Exception:
        logger.warning("주문 #%d 취소 실패(상태 유지): %s", order_id, reason)
        raise
    if not res.accepted:
        logger.warning("주문 #%d 취소 거부(상태 유지): %s", order_id, res.message)
        return {"ok": False, "order_id": order_id, "status": row["status"], "message": res.message}
    set_order_status(deps.conn, order_id, "cancelled")
    logger.info("주문 #%d 취소: %s", order_id, reason)
    return {"ok": True, "order_id": order_id, "status": "cancelled"}


def transmit(
    deps: ExecDeps, intent: OrderIntent, *, run_dir: Path, mode: str, broker: Any | None = None
) -> dict:
    """주문 1건을 브로커로 보내고 기록한다(옛 `_place`).

    실행 키(`intent.idem_key`, 빈 문자열이면 키 없음)는 브로커 호출 **직전** `mark_sent` 로
    표시한다 — 그 뒤로는 응답이 없어도 KIS 에 주문이 있을 수 있다. 브로커가 거부·실패한 주문은
    키를 놓고(KIS 에 주문 없음 → 재시도 허용), 접수·unknown 은 키를 남겨 주문 행에 묶는다.
    접수·unknown 뒤 원장 기록이 실패하면 CRITICAL + 알림 뒤 `OrderBookkeepingError` 를 올린다.
    `broker` 를 주면 그것을 쓰고(승인 실행이 UPDATE 전에 미리 얻은 것), 없으면 `broker_of`.
    반환 {status, order_id, order_no, message, request, result}."""
    key = intent.idem_key
    request = intent.request()
    shown = deps.label(intent.ticker, intent.name)
    if broker is None:
        broker = deps.broker_of(intent.account_id)
    if broker is None:
        raise ValueError(f"계좌 {intent.account_id} 브로커 없음")
    if key:
        deps.idem.mark_sent(key, sent_at=deps.now().isoformat())
    try:
        result = broker.place_order(request)
        status = "accepted" if result.accepted else "rejected"
    except KisApiError as e:
        result = OrderResult(order_no=None, accepted=False, message=f"{e.msg_cd}: {e.msg1}")
        if e.msg_cd == UNKNOWN_RESPONSE_CODE:
            # 요청은 나갔는데 응답이 없다: KIS 에 접수됐을 수 있어 failed 로 적으면 중복 방지
            # 조건(failed 제외)이 놓쳐 같은 주문을 다시 낸다. unknown 으로 살려 두고 체결
            # 동기화가 같은 날·종목·방향 체결로 확정한다.
            status = "unknown"
            logger.error("주문 응답 없음 %s %s: %s", intent.account_id, intent.ticker, e)
            notify_quietly(
                deps.notifier,
                messages.order_unknown(
                    account=intent.account_id,
                    market=intent.market,
                    label=shown,
                    side=intent.side,
                    qty=intent.qty,
                    err=e.msg1,
                ),
            )
        else:
            status = "failed"
            logger.warning("주문 실패 %s %s: %s", intent.account_id, intent.ticker, e)
    except ValueError as e:  # 거래소 코드 누락·계좌 시장 불일치 등 요청 자체의 오류
        result = OrderResult(order_no=None, accepted=False, message=f"INVALID: {e}")
        status = "failed"
        logger.warning("주문 요청 오류 %s %s: %s", intent.account_id, intent.ticker, e)
    if key and status in ("rejected", "failed"):
        try:
            deps.idem.release(key)
        except Exception as e:  # noqa: BLE001 - 키가 남으면 재시도만 막힌다(중복 주문은 아님)
            logger.warning("실행 키 해제 실패 %s: %s", key, e)
    sizing_price = intent.price if intent.price is not None else intent.quote_price
    out = {
        "status": status,
        "order_id": None,
        "order_no": result.order_no,
        "message": result.message,
        "request": request.model_dump(mode="json"),
        "result": result.model_dump(mode="json"),
    }
    # orders 행은 멱등의 근거라 접수 뒤 반드시 남아야 한다. 기록이 실패하면 접수번호를
    # 알림·CRITICAL 로그에 남기고 올린다(조용히 묻히면 재실행 때 같은 주문을 다시 낸다).
    try:
        order_id = insert_order(
            deps.conn,
            decision_id=intent.decision_id,
            account_id=intent.account_id,
            ticker=intent.ticker,
            market=intent.market,
            side=intent.side,
            qty=intent.qty,
            order_type=intent.order_type,
            price=intent.price,
            status=status,
            order_no=result.order_no,
            message=result.message,
            mode=mode,
            created_at=deps.now().isoformat(),
            amount=Decimal(intent.qty) * sizing_price if sizing_price is not None else None,
            org_no=result.org_no,
            asset=intent.asset,
            origin=intent.origin,
        )
    except Exception as e:
        if status not in ("accepted", "unknown"):
            # 브로커가 거부·실패한 주문은 KIS 에 없으므로 행이 빠져도 추적할 것이 없다
            logger.error(
                "실패 주문의 기록 실패 %s %s (%s): %s", intent.account_id, intent.ticker, status, e
            )
            return out
        logger.critical(
            "주문 기록 실패 %s %s %s %d주 접수번호 %s: %s",
            intent.account_id,
            intent.side,
            intent.ticker,
            intent.qty,
            result.order_no,
            e,
        )
        notify_quietly(
            deps.notifier,
            messages.bookkeeping_failure(
                account=intent.account_id,
                label=shown,
                side=intent.side,
                qty=intent.qty,
                order_no=result.order_no,
                status=status,
                err=e,
            ),
        )
        raise OrderBookkeepingError(result.order_no, e) from e
    out["order_id"] = order_id
    if key and status in ("accepted", "unknown"):
        try:
            deps.idem.bind(key, order_id=order_id, at=deps.now().isoformat())
        except Exception as e:  # noqa: BLE001 - 키 자체는 이미 잡혀 있다(묶음은 추적용)
            logger.warning("실행 키 묶기 실패 %s: %s", key, e)
    payload = {"request": out["request"], "result": out["result"], "mode": mode, "status": status}
    # 체크포인트·알림 실패는 원장에 이미 기록됐으므로 경고만 남긴다
    try:
        run_dir.mkdir(parents=True, exist_ok=True)
        write_checkpoint(
            run_dir, f"{intent.order_stage}_{intent.account_id}", checkpoint_payload(payload)
        )
    except OSError as e:
        logger.warning("주문 체크포인트 기록 실패 %s %s: %s", intent.account_id, intent.ticker, e)
    if status != "unknown":  # unknown 은 위에서 이미 알렸다(실패로 덧알리면 운영자가 재주문함)
        notify_quietly(
            deps.notifier,
            messages.order(
                account=intent.account_id,
                market=intent.market,
                label=shown,
                side=intent.side,
                qty=intent.qty,
                price=intent.price,
                mode=mode,
                accepted=result.accepted,
                order_no=result.order_no,
                message=result.message,
                opinion=messages.DecisionBrief.of(intent.opinion),
                kind=_kind(intent.order_stage),
            ),
        )
    return out


# --- 승인 요청 ----------------------------------------------------------------------------


def _approval_order(intent: OrderIntent) -> dict:
    """승인 행에 남길 주문 dict — `get_approval` 이 돌려주는 모양(사이드채널 합침, 문자열 값)과
    같게 만든다. 지문은 이 dict 로 계산하고, 승인 실행 때 `get_approval` 의 order 로 다시
    계산한다."""
    order = intent.request().model_dump(mode="json")
    if intent.name and intent.name != intent.ticker:
        order[NAME_KEY] = intent.name
    side = {
        "quote_price": None if intent.quote_price is None else str(intent.quote_price),
        "order_stage": intent.order_stage,
        "exec_key": intent.idem_key or None,
        "origin": intent.origin,
    }
    order.update({k: v for k, v in side.items() if v is not None})
    return order


def request_approval(
    deps: ExecDeps,
    intent: OrderIntent,
    *,
    env: str,
    ttl: timedelta = APPROVAL_TTL,
    reasons: Sequence[str] = (),
    buttons_of: Callable[[int, str], list | None] | None = None,
    link_of: Callable[[int], str | None] | None = None,
) -> int:
    """승인 요청을 pending 으로 저장하고 실행 키를 묶은 뒤 알린다. 승인 id 를 돌려준다.

    `buttons_of(approval_id, env)` 는 알림에 붙일 버튼 행(기능이 정한 callback_data),
    `link_of(decision_id)` 는 본문 끝 1줄 링크(결정 id 가 없으면 부르지 않는다). 둘 다 None 이면
    옛 평문 그대로다 — 커널은 문구만 만들고 버튼·링크의 뜻은 모른다.

    시장가는 금액을 모르므로 요청 시 현재가(quote_price)를 남겨 승인 뒤 주문의 amount 로 쓴다.
    주문 단계(order_stage)·실행 키·origin 도 남겨 승인 실행이 이어 쓴다. `reasons` 는 리스크 판정
    사유(알림 표시용).

    알림 실패는 경고로만 남긴다 — 이미 만든 승인 요청을 되돌리지 않는다. 이 함수가 예외를 올리면
    (승인 행 생성 전·묶기 실패 등) 실행 키 해제는 호출자가 하되 **`not idem.has_approval(key)` 일
    때만** 푼다. 승인에 묶인 키를 풀면 다른 경로가 같은 키를 잡고, 사람이 남은 승인까지 실행하면
    같은 주문이 두 번 나간다."""
    now = deps.now()
    expires = (now + ttl).isoformat()
    order = _approval_order(intent)
    aid = create_approval(
        deps.conn,
        decision_id=intent.decision_id or 0,
        account_id=intent.account_id,
        order=order,
        requested_at=now.isoformat(),
        expires_at=expires,
        fingerprint=approval_fingerprint(intent.account_id, intent.decision_id, order),
        stage=intent.order_stage,
        exec_key=intent.idem_key or None,
        origin=intent.origin,
        quote_price=intent.quote_price,
    )
    if intent.idem_key:
        deps.idem.bind(intent.idem_key, approval_id=aid, at=now.isoformat())
    text = messages.approval_request(
        approval_id=aid,
        account=intent.account_id,
        market=intent.market,
        label=deps.label(intent.ticker, intent.name),
        side=intent.side,
        qty=intent.qty,
        price=intent.price,
        reasons=list(reasons),
        opinion=messages.DecisionBrief.of(intent.opinion),
        kind=_kind(intent.order_stage),
        env=env,
        quote_price=intent.quote_price,
        expires_at=expires,
    )
    _notify_approval(deps, intent, aid, env, text, buttons_of, link_of)
    return aid


def _notify_approval(
    deps: ExecDeps,
    intent: OrderIntent,
    approval_id: int,
    env: str,
    text: str,
    buttons_of: Callable[[int, str], list | None] | None,
    link_of: Callable[[int], str | None] | None,
) -> None:
    """승인 알림 전송. 버튼·링크 콜백 실패나 버튼 데이터 거부(ValueError)는 평문으로 다시 보낸다 —
    승인 요청 알림이 버튼 때문에 사라지지 않게. 전송 실패는 경고만(요청은 되돌리지 않는다)."""
    buttons = None
    try:
        link = link_of(intent.decision_id) if link_of and intent.decision_id else None
        if link:
            text = f"{text}\n{link}"
        buttons = buttons_of(approval_id, env) if buttons_of else None
    except Exception as e:  # noqa: BLE001 - 꾸밈 실패가 알림을 막지 않는다
        logger.warning("승인 알림 버튼·링크 생성 실패 #%s: %s", approval_id, type(e).__name__)
    msg = Message(text, kind="approval", account_id=intent.account_id, buttons=buttons)
    try:
        send_notice(deps.notifier, msg)
        return
    except ValueError as e:  # 버튼 데이터 한도 초과 등 — 평문으로
        logger.warning("승인 알림 버튼 거부 #%s: %s — 평문으로 보냄", approval_id, e)
    except Exception as e:  # noqa: BLE001 - 알림 채널 장애는 승인 요청과 무관
        logger.warning("알림 전송 실패: %s", type(e).__name__)
        return
    notify_quietly(deps.notifier, text)


# --- 승인 처리 ----------------------------------------------------------------------------


def _fail(approval_id: int, reason: str, status: str | None, error: str) -> dict:
    return {
        "ok": False, "approval_id": approval_id, "status": status, "reason": reason,
        "error": error,
    }  # fmt: skip


def _take_pending(deps: ExecDeps, approval_id: int) -> tuple[dict | None, dict | None]:
    """pending·미만료 승인 행 또는 (None, 실패 결과). 만료 시각이 지났으면 expired 로 바꾼다."""
    a = get_approval(deps.conn, approval_id)
    if a is None:
        return None, _fail(approval_id, "missing", "expired", f"승인 요청 #{approval_id} 없음")
    if a["status"] != "pending":
        return None, _fail(
            approval_id, "not_pending", a["status"],
            f"승인 요청 #{approval_id} 은 이미 처리됨: {a['status']}",
        )  # fmt: skip
    now = deps.now()
    if datetime.fromisoformat(a["expires_at"]) <= now:
        decide_approval(deps.conn, approval_id, "expired", decided_at=now.isoformat())
        return None, _fail(
            approval_id, "expired", "expired", f"승인 요청 #{approval_id} 은 만료됨 (30분 초과)"
        )
    return a, None


def _expire(deps: ExecDeps, approval_id: int) -> None:
    decide_approval(deps.conn, approval_id, "expired", decided_at=deps.now().isoformat())


def _intent_of(a: dict, order: dict, opinion: dict | None) -> OrderIntent:
    """승인 행의 order(사이드채널 합침)를 주문 의도로 되돌린다."""
    raw_quote = order.get("quote_price")
    raw_price = order.get("price")
    return OrderIntent(
        account_id=a["account_id"],
        ticker=str(order["ticker"]),
        market=str(order["market"]),
        exchange=order.get("exchange"),
        side=str(order["side"]),
        qty=int(order["qty"]),
        order_type=str(order["order_type"]),
        price=None if raw_price is None else Decimal(str(raw_price)),
        origin=str(order.get("origin") or "llm"),
        risk_stage="",
        order_stage=str(order.get("order_stage") or "50_order"),
        idem_key=str(order.get("exec_key") or ""),
        decision_id=a["decision_id"] or None,
        quote_price=None if raw_quote is None else Decimal(str(raw_quote)),
        name=order.get(NAME_KEY),
        opinion=opinion,
    )


def _key_sent(deps: ExecDeps, key: str | None) -> bool:
    """실행 키에 전송 표시(`sent_at`)가 있나 — 있으면 브로커 호출까지 갔다."""
    if not key:
        return False
    row = deps.conn.execute("SELECT sent_at FROM exec_locks WHERE key=?", (key,)).fetchone()
    return row is not None and row[0] is not None


def approve(
    deps: ExecDeps,
    approval_id: int,
    *,
    policy_of: Callable[[str], ExecPolicy | None],
    run_dir_of: Callable[[dict], Path],
    decision_side_of: Callable[[int], str | None] | None = None,
    recheck: Callable[[dict, dict], str | KeepPending | None] | None = None,
    opinion_of: Callable[[dict], dict | None] | None = None,
) -> dict:
    """pending·미만료 승인 요청을 주문으로 실행한다. 두 번째 호출은 거부된다.

    순서(옛 executor.approve): ① 없음·처리됨·만료 ② 일시정지 → pending 유지 ③ 계좌 없음(정책 None)
    → 만료 ④ 모드가 approve 아님 → pending 유지 ⑤ 지문 불일치 → failed + 긴급 알림 ⑥ 결정 방향이
    바뀜(`decision_side_of(decision_id)` 가 값을 주고 주문 방향과 다름) → 만료 ⑦ 브로커 없음 →
    pending 유지 ⑧ `recheck(approval, order)` ⑨ 조건부 UPDATE 로 approved
    (다른 프로세스가 먼저 처리했으면 race, 그 사이 만료 시각이 지났으면 expired)
    ⑩ `transmit(mode="approve")`. 기록 실패
    (`OrderBookkeepingError`)는 승인을 failed 로 바꾸고 다시 올린다. 브로커 거부·실패는 failed,
    unknown 은 approved 로 둔다(KIS 에 접수됐을 수 있다).

    approved 로 바꾸기(⑨) **전에** 주문 요청 검증·`run_dir_of`·`opinion_of`·브로커 조회를 끝낸다 —
    UPDATE 뒤에 예외가 나면 주문 없는 approved 가 굳기 때문이다. 주문 dict 가 주문으로 복원되지
    않으면 failed(`invalid_order`), `run_dir_of`·`opinion_of` 예외는 pending 유지(`prepare`).

    `recheck` 반환 세 갈래: `None` → 계속 진행, `str`(빈 문자열 포함) → 만료(주문 없음),
    `KeepPending(reason)` → 지금은 판단 불가(예: 잔고 조회 실패) — 승인은 pending 그대로 두고
    `{ok: False, approval_id, status: "pending", reason}` 를 돌려준다(다음 승인 시도가 다시 본다).

    `opinion_of(approval)` 은 주문 알림에 붙일 결정 의견 dict(승인 행 전체를 받는다 — decision_id 와
    order 가 함께 있다). 반환 {ok, approval_id, status(승인 상태), reason?, error?, order?}."""
    a, err = _take_pending(deps, approval_id)
    if a is None:
        return err  # type: ignore[return-value]
    account_id = a["account_id"]
    policy = policy_of(account_id)
    if policy is not None and policy.paused:
        return _fail(
            approval_id, "paused", "pending",
            "일시정지 중이라 승인을 실행하지 않음 (요청은 pending 유지)",
        )  # fmt: skip
    if policy is None:
        _expire(deps, approval_id)
        return _fail(approval_id, "no_account", "expired", f"계좌 {account_id} 가 설정에 없음")
    if policy.dry_run:  # dry-run 데몬: 실주문 때 만든 요청이 남아 있어도 전송하지 않는다
        return _fail(
            approval_id, "dry_run", "pending",
            "dry-run 실행 중이라 승인을 실행하지 않음 (요청은 pending 유지)",
        )  # fmt: skip
    if policy.mode != "approve":  # 요청 뒤 off 로 내렸으면 승인해도 주문이 나가면 안 된다
        return _fail(
            approval_id, "mode", "pending",
            f"계좌 모드가 approve 가 아님({policy.mode}): 승인 미실행 (요청은 pending 유지)",
        )  # fmt: skip
    order = dict(a["order"])
    shown = deps.label(str(order.get("ticker")), order.get(NAME_KEY))
    # v5 의 fingerprint 는 NOT NULL 이라 항상 비교한다
    if a["fingerprint"] != approval_fingerprint(account_id, a["decision_id"], order):
        logger.critical("승인 지문 불일치 #%s %s", approval_id, account_id)
        decide_approval(deps.conn, approval_id, "failed", decided_at=deps.now().isoformat())
        notify_quietly(
            deps.notifier,
            messages.approval_tampered(approval_id=approval_id, account=account_id, label=shown),
        )
        return _fail(
            approval_id, "tampered", "failed",
            f"승인 요청 #{approval_id} 지문 불일치: 주문 내용이 바뀜",
        )  # fmt: skip
    try:
        intent = _intent_of(a, order, None)
        intent.request()  # OrderRequest 검증(extra=forbid·Literal) — UPDATE 전에
    except (KeyError, TypeError, ValueError, ArithmeticError) as e:
        logger.error("승인 주문 복원 실패 #%s %s: %s", approval_id, account_id, e)
        decide_approval(deps.conn, approval_id, "failed", decided_at=deps.now().isoformat())
        return _fail(
            approval_id, "invalid_order", "failed",
            f"승인 요청 #{approval_id} 주문 내용을 복원하지 못함: {e}",
        )  # fmt: skip
    # 요청 뒤 재평가가 같은 결정을 반대 방향·관망으로 바꿨으면 옛 요청을 실행하지 않는다
    if decision_side_of is not None and a["decision_id"]:
        want = decision_side_of(a["decision_id"])
        if want is not None and want != intent.side:
            _expire(deps, approval_id)
            return _fail(
                approval_id, "decision_changed", "expired",
                f"승인 요청 #{approval_id} 뒤 결정이 {want} 로 바뀌어 실행하지 않음 "
                "(요청은 만료 처리)",
            )  # fmt: skip
    broker = deps.broker_of(account_id)
    if broker is None:
        return _fail(
            approval_id, "no_broker", "pending",
            f"계좌 {account_id} 브로커 없음(앱키 미설정): 요청은 pending 유지",
        )  # fmt: skip
    try:
        run_dir = run_dir_of(a)
        opinion = opinion_of(a) if opinion_of is not None else None
    except Exception as e:  # noqa: BLE001 - 준비 실패는 주문 전이라 다음 시도에 맡긴다
        logger.warning("승인 실행 준비 실패 #%s %s: %s", approval_id, account_id, e)
        return _fail(
            approval_id, "prepare", "pending",
            f"승인 요청 #{approval_id} 실행 준비 실패({type(e).__name__}): 요청은 pending 유지",
        )  # fmt: skip
    if opinion is not None:
        intent = _intent_of(a, order, opinion)
    if recheck is not None:
        why = recheck(a, order)
        if isinstance(why, KeepPending):
            return _fail(
                approval_id, why.reason, "pending",
                f"승인 요청 #{approval_id} 재판정 불가({why.reason}): 요청은 pending 유지",
            )  # fmt: skip
        if isinstance(why, str):
            _expire(deps, approval_id)
            return _fail(
                approval_id, "recheck", "expired",
                f"승인 요청 #{approval_id} 실행 직전 재판정: {why} (요청은 만료 처리)",
            )  # fmt: skip
    # 조건부 UPDATE: 다른 프로세스(텔레그램 ↔ CLI ↔ 웹)가 먼저 처리했거나, 재판정 사이에
    # 만료 시각이 지났으면 주문 없음
    now = deps.now().isoformat()
    if not decide_approval(deps.conn, approval_id, "approved", decided_at=now, unexpired_at=now):
        current = get_approval(deps.conn, approval_id)
        if current is not None and current["status"] == "pending":  # 처리된 게 아니라 만료
            decide_approval(deps.conn, approval_id, "expired", decided_at=now)
            return _fail(
                approval_id, "expired", "expired",
                f"승인 요청 #{approval_id} 은 실행 직전 만료됨 (30분 초과)",
            )  # fmt: skip
        return _fail(
            approval_id, "race", None,
            f"승인 요청 #{approval_id} 은 이미 다른 프로세스가 처리함",
        )  # fmt: skip
    try:
        result = transmit(deps, intent, run_dir=run_dir, mode="approve", broker=broker)
    except OrderBookkeepingError:  # 접수 뒤 기록 실패: approved 로 굳지 않게 failed 로 남긴다
        decide_approval(deps.conn, approval_id, "failed", expect="approved")
        raise
    except Exception as e:
        # 그 밖의 예외: 브로커로 보내기 전(원장 잠금 등)이면 주문이 없다 → failed 로 남긴다(주문
        # 없는 "승인됨" 은 알아챌 길이 없다). 보낸 뒤면 KIS 에 접수됐을 수 있다 → "실패" 로 적으면
        # 사람이 다시 주문하므로 approved 로 두고 확인하라고 알린다. 실행 키는 어느 쪽도 풀지 않는다
        try:
            if _key_sent(deps, intent.idem_key):
                notify_quietly(
                    deps.notifier,
                    messages.order_unknown(
                        account=account_id, market=intent.market, label=shown, side=intent.side,
                        qty=intent.qty, err=f"{type(e).__name__}: {e}",
                    ),
                )  # fmt: skip
            else:
                decide_approval(deps.conn, approval_id, "failed", expect="approved")
        except Exception as inner:  # noqa: BLE001 - 원래 예외를 가리지 않는다
            logger.error("승인 #%s 뒤처리 실패: %s", approval_id, inner)
        raise
    accepted = bool(result["result"]["accepted"])
    status = "approved"
    if not accepted and result["status"] != "unknown":
        # unknown(응답 없음)은 KIS 에 접수됐을 수 있어 failed 로 적지 않는다 — 운영자가 재주문함
        decide_approval(deps.conn, approval_id, "failed", expect="approved")
        status = "failed"
    return {"ok": accepted, "approval_id": approval_id, "status": status, "order": result}


def reject(deps: ExecDeps, approval_id: int) -> dict:
    """pending·미만료 승인 요청을 거부하고 알린다.
    반환 {ok, approval_id, status, reason?, error?}."""
    a, err = _take_pending(deps, approval_id)
    if a is None:
        return err  # type: ignore[return-value]
    if not decide_approval(deps.conn, approval_id, "rejected", decided_at=deps.now().isoformat()):
        return _fail(
            approval_id, "race", None, f"승인 요청 #{approval_id} 은 이미 다른 프로세스가 처리함"
        )
    o = a["order"]
    deps.notifier.send(
        messages.approval_rejected(
            approval_id=approval_id,
            account=a["account_id"],
            label=deps.label(str(o.get("ticker")), o.get(NAME_KEY)),
        )
    )
    return {"ok": True, "approval_id": approval_id, "status": "rejected"}


def expire_approvals(deps: ExecDeps) -> list[int]:
    """만료 시각이 지난 pending 승인을 expired 로 바꾸고 id 목록을 돌려준다."""
    return expire_pending(deps.conn, deps.now().isoformat())
