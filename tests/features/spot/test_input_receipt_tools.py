"""도구의 최종 축약 이후 기록하며 기존 응답/분석 결과를 바꾸지 않는다."""

import hashlib
import json

import pytest

from tests.features.spot.conftest import D
from tests.features.spot.test_tools import _realistic_reports_and_turns
from tests.features.spot.test_tools import ctx as ctx  # noqa: F401
from trader.features.spot import tools
from trader.kernel.session import input_receipts, run_store


@pytest.mark.parametrize("kind", ["full", "analyst", "researcher", "trader"])
def test_receipt_matches_final_tool_response_and_failure_preserves_result(
    ctx, project, monkeypatch, kind
):
    _realistic_reports_and_turns(ctx, project)
    rd = run_store.run_dir(D, "KR", "005930")
    before = (rd / "00_inputs.json").read_bytes()

    def call():
        if kind == "full":
            return tools.checkpoint_read(
                ctx, ticker="005930", market="KR", date=D, stage="00_inputs"
            )
        if kind == "analyst":
            return tools.checkpoint_read(
                ctx, ticker="005930", market="KR", date=D, stage="00_inputs", section="sentiment"
            )
        return tools.checkpoint_bundle(
            ctx,
            ticker="005930",
            market="KR",
            date=D,
            kind=kind,
            **({"round": 0, "side": "bull"} if kind == "researcher" else {}),
        )

    result = call()
    context = result["data"] if kind == "full" else result["data"]["input_context"]
    digest = hashlib.sha256(
        json.dumps(context, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    files = list((rd / "input_receipts").glob("*.json"))
    assert len(files) == 1
    receipt = json.loads(files[0].read_text())
    assert receipt["context_hash"] == digest
    assert receipt["provider"] is None  # 도구 호출자의 모델은 역할만으로 추정하지 않는다.
    assert (rd / "00_inputs.json").read_bytes() == before
    monkeypatch.setattr(input_receipts, "record", lambda *args, **kwargs: False)
    assert call() == result
