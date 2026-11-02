"""execution 코어: transmit·승인 요청·approve/reject/expire(옛 executor `_place`·승인 처리)."""

import json
import logging
import sqlite3
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from trader.kernel.broker.errors import UNKNOWN_RESPONSE_CODE, KisApiError
from trader.kernel.broker.models import OrderResult
from trader.kernel.execution import core
from trader.kernel.execution.idem import ExecKeyStore
from trader.kernel.execution.ledger import get_approval
from trader.kernel.execution.types import APPROVAL_TTL, ExecPolicy, OrderBookkeepingError

T0 = datetime(2026, 11, 2, 1, 0, tzinfo=UTC)
ACCT = "paper-main"
KEY = "7|paper-main|BUY|50_order"
APPROVE = ExecPolicy(mode="approve", note=None, paused=False, dry_run=False)


class Sent:
    def __init__(self):
        self.sent: list[str] = []

    def send(self, text, *, escape=True):
        self.sent.append(text)
        return True


class Broker:
    """place_order 결과를 고를 수 있는 가짜 브로커(`result` 또는 `exc`)."""

    def __init__(self, result=None, exc=None, on_call=None):
        self.result = result or OrderResult(
            order_no="0001", accepted=True, message="정상 처리", org_no="06010"
        )
        self.exc = exc
        self.on_call = on_call
        self.orders = []

    def place_order(self, request):
        if self.on_call:
            self.on_call(request)
        self.orders.append(request)
        if self.exc is not None:
            raise self.exc
        return self.result


class Clock:
    def __init__(self, t=T0):
        self.t = t

    def __call__(self):
        return self.t


@pytest.fixture
def broker():
    return Broker()


@pytest.fixture
def deps(conn, broker):
    return core.ExecDeps(
        conn=conn,
        notifier=Sent(),
        now=Clock(),
        idem=ExecKeyStore(conn),
        broker_of=lambda aid: broker if aid == ACCT else None,
        label=lambda t, n: f"{n}({t})" if n else t,
    )


def _intent(**kw):
    base = dict(
        account_id=ACCT, ticker="005930", market="KR", exchange=None, side="BUY", qty=3,
        order_type="LIMIT", price=Decimal("71500"), origin="llm", risk_stage="40_risk_check",
        order_stage="50_order", idem_key=KEY, decision_id=7, quote_price=None, name="삼성전자",
        opinion=None,
    )  # fmt: skip
    base.update(kw)
    return core.OrderIntent(**base)


def _lock(conn, key=KEY):
    return conn.execute("SELECT * FROM exec_locks WHERE key=?", (key,)).fetchone()


def _orders(conn):
    return conn.execute("SELECT * FROM orders ORDER BY id").fetchall()


def _status(conn, aid):
    return get_approval(conn, aid)["status"]


def _request(deps, **kw):
    intent = _intent(**kw)
    assert deps.idem.claim(intent.idem_key, now=T0.isoformat())
    return core.request_approval(deps, intent, env="paper")


# 승인 실행이 체크포인트를 쓰는 run 디렉터리(테스트마다 _run_dir 픽스처가 채운다)
_RUN_DIR: list = []


def _approve(deps, aid, policy=APPROVE, **kw):
    return core.approve(
        deps, aid, policy_of=lambda _a: policy, run_dir_of=lambda _a: _RUN_DIR[0], **kw
    )


@pytest.fixture(autouse=True)
def _run_dir(tmp_path):
    d = tmp_path / "runs" / "005930"
    d.mkdir(parents=True)
    _RUN_DIR[:] = [d]
    return d


# --- transmit ------------------------------------------------------------------------------


def test_transmit_accepted_inserts_order_binds_key_writes_checkpoint_and_notifies(
    deps, conn, _run_dir
):
    assert deps.idem.claim(KEY, now=T0.isoformat())
    opinion = {"action": "BUY", "target_weight": 0.15, "confidence": 0.62}
    out = core.transmit(
        deps,
        _intent(order_stage="51_order_r1", opinion=opinion, origin="stop"),
        run_dir=_run_dir,
        mode="auto",
    )
    assert out["status"] == "accepted" and out["order_no"] == "0001"
    assert out["message"] == "정상 처리" and out["result"]["accepted"] is True
    assert out["request"]["ticker"] == "005930"
    (row,) = _orders(conn)
    assert out["order_id"] == row["id"]
    assert (row["status"], row["mode"], row["origin"], row["asset"]) == (
        "accepted", "auto", "stop", "stock",
    )  # fmt: skip
    assert Decimal(row["amount"]) == Decimal("214500") and row["org_no"] == "06010"
    assert row["decision_id"] == 7 and row["created_at"] == T0.isoformat()
    lock = _lock(conn)
    assert lock["order_id"] == row["id"] and lock["sent_at"] == T0.isoformat()
    cp = json.loads((_run_dir / f"51_order_r1_{ACCT}.json").read_text(encoding="utf-8"))
    assert cp["schema_version"] == 1
    assert cp["data"]["mode"] == "auto" and cp["data"]["status"] == "accepted"
    assert cp["data"]["request"]["qty"] == 3 and cp["data"]["result"]["order_no"] == "0001"
    (msg,) = deps.notifier.sent
    # 체크포인트 접두 '51_order' → 제목 꼬리 '재평가', 의견은 접수일 때만
    assert msg.startswith("[주문 · 재평가] paper-main · 삼성전자(005930)")
    assert "매수 3주 @71,500원 · 자동" in msg and "최종 의견: 매수" in msg


def test_transmit_market_order_amount_uses_quote_price_and_checkpoint_oserror_is_swallowed(
    deps, conn, tmp_path, caplog
):
    missing = tmp_path / "file-not-dir"
    missing.write_text("x", encoding="utf-8")  # 디렉터리 자리에 파일 → OSError
    with caplog.at_level(logging.WARNING, logger="trader.execution"):
        out = core.transmit(
            deps,
            _intent(order_type="MARKET", price=None, quote_price=Decimal("70000"), idem_key=""),
            run_dir=missing,
            mode="auto",
        )
    assert out["status"] == "accepted"
    (row,) = _orders(conn)
    assert Decimal(row["amount"]) == Decimal("210000") and row["price"] is None
    assert "체크포인트 기록 실패" in caplog.text


def test_transmit_rejected_releases_key_and_records_failed_row(deps, conn, broker, _run_dir):
    broker.result = OrderResult(order_no=None, accepted=False, message="40100000: 장 운영 아님")
    assert deps.idem.claim(KEY, now=T0.isoformat())
    out = core.transmit(deps, _intent(), run_dir=_run_dir, mode="auto")
    assert out["status"] == "rejected" and _lock(conn) is None
    broker.exc = KisApiError("EGW00123", "잔고 부족")
    assert deps.idem.claim(KEY, now=T0.isoformat())  # 거부로 풀린 키는 다시 잡힌다
    out = core.transmit(deps, _intent(), run_dir=_run_dir, mode="auto")
    assert out["status"] == "failed" and out["message"] == "EGW00123: 잔고 부족"
    assert _lock(conn) is None
    assert [r["status"] for r in _orders(conn)] == ["rejected", "failed"]
    assert all(m.startswith("[주문 실패] paper-main") for m in deps.notifier.sent)
    assert "최종 의견" not in deps.notifier.sent[0]
    # 요청 자체의 오류(ValueError)도 failed
    broker.exc = ValueError("거래소 코드 없음")
    assert deps.idem.claim(KEY, now=T0.isoformat())
    out = core.transmit(deps, _intent(), run_dir=_run_dir, mode="auto")
    assert out["status"] == "failed" and out["message"].startswith("INVALID: ")
    assert _lock(conn) is None


def test_transmit_unknown_keeps_key_and_notifies_unknown(deps, conn, broker, _run_dir):
    broker.exc = KisApiError(UNKNOWN_RESPONSE_CODE, "응답 시간 초과")
    assert deps.idem.claim(KEY, now=T0.isoformat())
    out = core.transmit(deps, _intent(), run_dir=_run_dir, mode="auto")
    assert out["status"] == "unknown"
    (row,) = _orders(conn)
    assert row["status"] == "unknown"
    lock = _lock(conn)
    assert lock is not None and lock["order_id"] == row["id"] and lock["sent_at"]
    (msg,) = deps.notifier.sent  # 실패 알림을 덧붙이지 않는다(운영자가 재주문하게 됨)
    assert msg.startswith("[주문 상태 미확인] paper-main · 삼성전자(005930)")
    assert "사유: 응답 시간 초과" in msg
    assert (_run_dir / f"50_order_{ACCT}.json").exists()


def _broken_insert(*a, **k):
    raise sqlite3.OperationalError("database is locked")


def test_transmit_bookkeeping_failure_is_critical_and_raises_for_accepted(
    deps, conn, _run_dir, monkeypatch, caplog
):
    monkeypatch.setattr(core, "insert_order", _broken_insert)
    assert deps.idem.claim(KEY, now=T0.isoformat())
    with caplog.at_level(logging.WARNING, logger="trader.execution"):
        with pytest.raises(OrderBookkeepingError) as ei:
            core.transmit(deps, _intent(), run_dir=_run_dir, mode="auto")
    assert ei.value.order_no == "0001"
    assert isinstance(ei.value.__cause__, sqlite3.OperationalError)
    assert any(r.levelno == logging.CRITICAL for r in caplog.records)
    # 보낸 키는 남는다(사람이 KIS 와 대조) — 주문 행이 없으니 order_id 도 없다
    lock = _lock(conn)
    assert lock is not None and lock["sent_at"] and lock["order_id"] is None
    (msg,) = deps.notifier.sent
    assert msg.startswith("[긴급: 주문 기록 실패] paper-main") and "접수번호 0001" in msg
    assert not (_run_dir / f"50_order_{ACCT}.json").exists()


def test_transmit_bookkeeping_failure_for_rejected_is_logged_only(
    deps, conn, broker, _run_dir, monkeypatch, caplog
):
    monkeypatch.setattr(core, "insert_order", _broken_insert)
    broker.result = OrderResult(order_no=None, accepted=False, message="거부")
    assert deps.idem.claim(KEY, now=T0.isoformat())
    with caplog.at_level(logging.WARNING, logger="trader.execution"):
        out = core.transmit(deps, _intent(), run_dir=_run_dir, mode="auto")
    assert out["status"] == "rejected" and out["order_id"] is None
    assert any(r.levelno == logging.ERROR for r in caplog.records)
    assert not any(r.levelno == logging.CRITICAL for r in caplog.records)
    assert deps.notifier.sent == [] and _lock(conn) is None


def test_transmit_marks_sent_before_broker_call(deps, conn, broker, _run_dir):
    seen = []
    broker.on_call = lambda req: seen.append(_lock(conn)["sent_at"])
    assert deps.idem.claim(KEY, now=T0.isoformat())
    assert _lock(conn)["sent_at"] is None
    core.transmit(deps, _intent(), run_dir=_run_dir, mode="auto")
    assert seen == [T0.isoformat()]


# --- 승인 요청 --------------------------------------------------------------------------------


def test_request_approval_creates_row_with_fingerprint_and_binds_key(deps, conn, broker):
    intent = _intent(
        order_stage="51_order_r2", origin="hedge", order_type="MARKET", price=None,
        quote_price=Decimal("70000"),
    )  # fmt: skip
    assert deps.idem.claim(KEY, now=T0.isoformat())
    aid = core.request_approval(deps, intent, env="real", reasons=["confidence<0.5 → 수량 반감"])
    a = get_approval(conn, aid)
    assert a["status"] == "pending" and a["decision_id"] == 7 and a["account_id"] == ACCT
    assert a["requested_at"] == T0.isoformat()
    assert a["expires_at"] == (T0 + APPROVAL_TTL).isoformat()
    assert (a["stage"], a["exec_key"], a["origin"], a["quote_price"]) == (
        "51_order_r2", KEY, "hedge", "70000",
    )  # fmt: skip
    # 지문은 get_approval 이 돌려주는 order(사이드채널 합침)로 다시 계산해도 같다
    assert a["fingerprint"] == core.approval_fingerprint(ACCT, 7, a["order"])
    assert a["order"]["name"] == "삼성전자" and a["order"]["qty"] == 3
    assert _lock(conn)["approval_id"] == aid and broker.orders == []
    (msg,) = deps.notifier.sent
    assert msg.startswith(f"[승인 요청 #{aid} · 실전 · 재평가] paper-main · 삼성전자(005930)")
    assert "시장가 = 210,000원 (현재가 기준)" in msg
    assert "리스크 판정: confidence<0.5 → 수량 반감" in msg
    assert f"/approve {aid} 실전 · /reject {aid} — 10:30 KST 까지" in msg
    # 결정 행 없는 주문(decision_id None)은 0 으로 남고 지문도 0 으로 계산한다
    key2 = "53_order_rebalance|paper-main|005930|BUY|2026-11-02"
    assert deps.idem.claim(key2, now=T0.isoformat())
    aid2 = core.request_approval(deps, _intent(decision_id=None, idem_key=key2), env="paper")
    a2 = get_approval(conn, aid2)
    assert a2["decision_id"] == 0
    assert a2["fingerprint"] == core.approval_fingerprint(ACCT, None, a2["order"])


# --- approve ------------------------------------------------------------------------------


def test_approve_executes_once_and_conditional_update_guards_race(deps, conn, broker):
    aid = _request(deps)
    r = _approve(deps, aid, opinion_of=lambda a: {"action": "BUY", "confidence": 0.7})
    assert r["ok"] is True and r["approval_id"] == aid and r["status"] == "approved"
    assert r["order"]["status"] == "accepted"
    assert _status(conn, aid) == "approved" and len(broker.orders) == 1
    (row,) = _orders(conn)
    assert row["mode"] == "approve" and _lock(conn)["order_id"] == row["id"]
    assert (_RUN_DIR[0] / f"50_order_{ACCT}.json").exists()
    assert "최종 의견: 매수" in deps.notifier.sent[-1]
    # 두 번째 호출은 pending 이 아니라 거부
    again = _approve(deps, aid)
    assert again["ok"] is False and again["reason"] == "not_pending"
    assert len(broker.orders) == 1
    # 경합: 재판정과 조건부 UPDATE 사이에 다른 프로세스가 먼저 처리했다
    key2 = "8|paper-main|BUY|50_order"
    aid2 = _request(deps, idem_key=key2, decision_id=8)

    def other_process(approval, order):
        conn.execute("UPDATE approvals SET status='approved' WHERE id=?", (approval["id"],))
        conn.commit()
        return None

    raced = _approve(deps, aid2, recheck=other_process)
    assert raced["ok"] is False and raced["reason"] == "race"
    assert len(broker.orders) == 1


def test_approve_rejected_order_marks_failed_but_unknown_stays_approved(deps, conn, broker):
    broker.result = OrderResult(order_no=None, accepted=False, message="거부")
    aid = _request(deps)
    r = _approve(deps, aid)
    assert r["ok"] is False and r["status"] == "failed" and _status(conn, aid) == "failed"
    broker.exc = KisApiError(UNKNOWN_RESPONSE_CODE, "응답 없음")
    aid2 = _request(deps, idem_key="9|paper-main|BUY|50_order", decision_id=9)
    r2 = _approve(deps, aid2)
    # unknown 은 KIS 에 접수됐을 수 있어 failed 로 적지 않는다
    assert r2["ok"] is False and r2["status"] == "approved"
    assert _status(conn, aid2) == "approved" and r2["order"]["status"] == "unknown"


def test_approve_bookkeeping_failure_marks_failed_and_reraises(deps, conn, monkeypatch):
    aid = _request(deps)
    monkeypatch.setattr(core, "insert_order", _broken_insert)
    with pytest.raises(OrderBookkeepingError):
        _approve(deps, aid)
    assert _status(conn, aid) == "failed"


def test_approve_expired_or_missing_marks_expired(deps, conn, broker):
    missing = _approve(deps, 999)
    assert missing["ok"] is False and missing["reason"] == "missing"
    assert missing["status"] == "expired"
    aid = _request(deps)
    deps.now.t = T0 + APPROVAL_TTL  # 만료 시각 도달
    r = _approve(deps, aid)
    assert r["ok"] is False and r["reason"] == "expired" and r["status"] == "expired"
    assert _status(conn, aid) == "expired" and broker.orders == []
    # 계좌가 설정에서 사라졌다(정책 없음) → 만료
    deps.now.t = T0
    aid2 = _request(deps, idem_key="8|paper-main|BUY|50_order", decision_id=8)
    gone = _approve(deps, aid2, policy=None)
    assert gone["reason"] == "no_account" and _status(conn, aid2) == "expired"
    assert broker.orders == []


def test_approve_paused_or_mode_changed_keeps_pending(deps, conn, broker):
    aid = _request(deps)
    paused = ExecPolicy(mode="approve", note=None, paused=True, dry_run=False)
    r = _approve(deps, aid, policy=paused)
    assert r["ok"] is False and r["reason"] == "paused" and r["status"] == "pending"
    off = ExecPolicy(mode="off", note="사용자가 끔", paused=False, dry_run=False)
    r = _approve(deps, aid, policy=off)
    assert r["ok"] is False and r["reason"] == "mode" and r["status"] == "pending"
    # 브로커가 없으면(앱키 미설정) pending 유지
    no_broker = core.ExecDeps(
        conn=conn, notifier=deps.notifier, now=deps.now, idem=deps.idem,
        broker_of=lambda _a: None,
    )  # fmt: skip
    r = _approve(no_broker, aid)
    assert r["ok"] is False and r["reason"] == "no_broker" and r["status"] == "pending"
    assert _status(conn, aid) == "pending" and broker.orders == []
    assert _approve(deps, aid)["ok"] is True  # 풀리면 그대로 실행된다


def test_approve_fingerprint_mismatch_fails_and_notifies(deps, conn, broker, caplog):
    aid = _request(deps)
    row = conn.execute("SELECT order_json FROM approvals WHERE id=?", (aid,)).fetchone()
    order = json.loads(row["order_json"])
    order["qty"] = int(order["qty"]) * 10
    conn.execute("UPDATE approvals SET order_json=? WHERE id=?", (json.dumps(order), aid))
    conn.commit()
    with caplog.at_level(logging.CRITICAL, logger="trader.execution"):
        r = _approve(deps, aid)
    assert r["ok"] is False and r["reason"] == "tampered" and r["status"] == "failed"
    assert "지문" in r["error"]
    assert broker.orders == [] and _status(conn, aid) == "failed"
    assert deps.notifier.sent[-1].startswith(
        f"[긴급: 승인 지문 불일치] 승인 요청 #{aid} · paper-main · 삼성전자(005930)"
    )
    assert any(r.levelno == logging.CRITICAL for r in caplog.records)
    # 사이드채널 열(stage 등)을 바꿔도 지문이 잡는다
    aid2 = _request(deps, idem_key="8|paper-main|BUY|50_order", decision_id=8)
    conn.execute("UPDATE approvals SET stage='55_order_sl' WHERE id=?", (aid2,))
    conn.commit()
    assert _approve(deps, aid2)["reason"] == "tampered"


def test_approve_recheck_reason_expires_and_reject_and_expire_all(deps, conn, broker):
    # 재판정 사유 → 만료(주문 없음)
    aid = _request(deps)
    seen = []

    def recheck(approval, order):
        seen.append((approval["id"], order["side"]))
        return "하락 국면(bear) 전환 — 신규 매수 금지"

    r = _approve(deps, aid, recheck=recheck)
    assert r["ok"] is False and r["reason"] == "recheck" and "하락 국면" in r["error"]
    assert seen == [(aid, "BUY")] and _status(conn, aid) == "expired" and broker.orders == []
    # 요청 뒤 결정 방향이 바뀌었다 → 만료
    aid2 = _request(deps, idem_key="8|paper-main|BUY|50_order", decision_id=8)
    r = _approve(deps, aid2, decision_side_of=lambda did: "SELL" if did == 8 else None)
    assert r["reason"] == "decision_changed" and _status(conn, aid2) == "expired"
    # 같은 방향이면 통과
    aid3 = _request(deps, idem_key="9|paper-main|BUY|50_order", decision_id=9)
    assert _approve(deps, aid3, decision_side_of=lambda did: "BUY")["ok"] is True
    # reject: 한 번만, 알림
    aid4 = _request(deps, idem_key="10|paper-main|BUY|50_order", decision_id=10)
    rj = core.reject(deps, aid4)
    assert rj == {"ok": True, "approval_id": aid4, "status": "rejected"}
    assert _status(conn, aid4) == "rejected"
    assert deps.notifier.sent[-1] == f"[거부] 승인 요청 #{aid4} · paper-main · 삼성전자(005930)"
    assert core.reject(deps, aid4)["ok"] is False
    assert core.reject(deps, 999)["reason"] == "missing"
    # expire_approvals: 만료 시각이 지난 pending 만
    aid5 = _request(deps, idem_key="11|paper-main|BUY|50_order", decision_id=11)
    deps.now.t = T0 + timedelta(minutes=5)
    aid6 = _request(deps, idem_key="12|paper-main|BUY|50_order", decision_id=12)
    deps.now.t = T0 + APPROVAL_TTL + timedelta(seconds=1)
    assert core.expire_approvals(deps) == [aid5]
    assert _status(conn, aid5) == "expired" and _status(conn, aid6) == "pending"


def test_approve_recheck_keep_pending_leaves_row_pending(deps, conn, broker):
    """Ruling 7: 재판정 불가(잔고 조회 실패 등)는 만료가 아니라 pending 유지."""
    aid = _request(deps)
    r = _approve(deps, aid, recheck=lambda a, o: core.KeepPending("balance"))
    assert {k: r[k] for k in ("ok", "approval_id", "status", "reason")} == {
        "ok": False, "approval_id": aid, "status": "pending", "reason": "balance",
    }  # fmt: skip
    assert "pending 유지" in r["error"] and "order" not in r
    assert _status(conn, aid) == "pending" and _orders(conn) == [] and broker.orders == []
    assert _lock(conn)["approval_id"] == aid
    assert _approve(deps, aid)["ok"] is True  # 다음 승인 시도는 그대로 실행된다
