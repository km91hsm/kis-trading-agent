"""주문·취소 단일 경로 계약: 기능 코드는 브로커의 주문·취소 API 를 직접 부르지 않는다.

`broker.place_order` 는 `kernel.execution.core.transmit`, `broker.cancel_order` 는
`kernel.execution.core.cancel` 만 부른다(스펙 §2.1, M5 Global Constraints).
예외 `ALLOWED`: fo 실행기는 다리 상태와 `orders` 행을 한 트랜잭션에 묶는 자체 전송기다(M10 A1).
그 밖의 fo 모듈은 `executor.run_leg/reduce/recall_entries` 를 거친다. 연결 점검 도구
`fo/order_test.py`·`accounts/order_test.py`(모의 전용, 원장 기록 없음 — M12 P16)도 예외다."""

import re
from pathlib import Path

import trader

FEATURES = Path(trader.__file__).parent / "features"
ALLOWED = frozenset({"fo/executor.py", "fo/order_test.py", "accounts/order_test.py"})


def _call_sites(name: str) -> list[str]:
    pat = re.compile(rf"\.{name}\(")
    hits = []
    for path in sorted(FEATURES.rglob("*.py")):
        if path.relative_to(FEATURES).as_posix() in ALLOWED:
            continue
        for no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if pat.search(line) and not line.lstrip().startswith("#"):
                hits.append(f"{path.relative_to(FEATURES)}:{no}: {line.strip()}")
    return hits


def test_features_do_not_call_broker_cancel_order():
    assert _call_sites("cancel_order") == []


def test_features_do_not_call_broker_place_order():
    assert _call_sites("place_order") == []
