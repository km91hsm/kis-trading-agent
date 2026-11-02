"""승인 지문(옛 tests/orchestrator/test_approval_fingerprint.py 의 순수 단언 이식)."""

from trader.kernel.execution.core import approval_fingerprint


def test_fingerprint_stable_key_order():
    a = approval_fingerprint("acct", 3, {"ticker": "005930", "qty": 2, "price": "1"})
    b = approval_fingerprint("acct", 3, {"price": "1", "qty": 2, "ticker": "005930"})
    assert a == b and len(a) == 64
    assert a != approval_fingerprint("acct", 3, {"ticker": "005930", "qty": 3, "price": "1"})
    # 표시용 이름은 지문에 들어가지 않는다
    assert a == approval_fingerprint(
        "acct", 3, {"ticker": "005930", "qty": 2, "price": "1", "name": "x"}
    )
    # 결정 없음(None)은 0 과 같다, 계좌가 다르면 다르다
    assert approval_fingerprint("acct", None, {}) == approval_fingerprint("acct", 0, {})
    assert approval_fingerprint("acct", 3, {}) != approval_fingerprint("other", 3, {})
