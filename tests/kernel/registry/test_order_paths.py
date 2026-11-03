"""주문·취소 단일 경로 계약: 기능 코드는 브로커의 주문·취소 API 를 직접 부르지 않는다.

`broker.place_order` 는 `kernel.execution.core.transmit`, `broker.cancel_order` 는
`kernel.execution.core.cancel` 만 부른다(스펙 §2.1, M5 Global Constraints)."""

import re
from pathlib import Path

import trader

FEATURES = Path(trader.__file__).parent / "features"


def _call_sites(name: str) -> list[str]:
    pat = re.compile(rf"\.{name}\(")
    hits = []
    for path in sorted(FEATURES.rglob("*.py")):
        for no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if pat.search(line) and not line.lstrip().startswith("#"):
                hits.append(f"{path.relative_to(FEATURES)}:{no}: {line.strip()}")
    return hits


def test_features_do_not_call_broker_cancel_order():
    assert _call_sites("cancel_order") == []


def test_features_do_not_call_broker_place_order():
    assert _call_sites("place_order") == []
