"""kernel/session/tooling — 체크포인트 도구 헬퍼."""

import json
import re

import pytest
from pydantic import ValidationError

from trader.kernel.session.run_store import checkpoint_payload, write_checkpoint
from trader.kernel.session.schemas import CallMeta
from trader.kernel.session.tooling import call_meta, load_turns, shrink


def _size(obj) -> int:
    return len(json.dumps(obj, ensure_ascii=False).encode("utf-8"))


def _report(role: str) -> dict:
    return {
        "role": role,
        "summary": "합성 요약",
        "evidence": [{"claim": "가" * 200, "source_id": f"s{i}"} for i in range(10)],
    }


def test_shrink_respects_byte_limit_and_drops_evidence_last():
    obj = {"reports": [_report("technical"), _report("fundamental")], "bars": [1, 2, 3]}
    # 한도 안이면 그대로(evidence 유지)
    assert shrink(obj, _size(obj)) == obj
    # 한도를 넘으면 문자열·목록을 자르기 전에 evidence 부터 뗀다
    limit = _size(obj) // 2
    out = shrink(obj, limit)
    assert _size(out) <= limit
    assert all("evidence" not in r for r in out["reports"])
    assert out["reports"][0]["summary"] == "합성 요약" and out["bars"] == [1, 2, 3]
    assert "note" not in out
    # evidence 를 떼고도 넘으면 긴 문자열을 자르고 note 를 남긴다
    big = {"reports": [{"role": "technical", "summary": "나" * 2000}]}
    cut = shrink(big, 600)
    assert _size(cut) <= 600 and "note" in cut


def _turn(side: str, rnd: int) -> dict:
    return {
        "side": side,
        "round": rnd,
        "stance": "BULLISH" if side == "bull" else "BEARISH",
        "valuation_view": "UNKNOWN",
        "arguments": [],
        "rebuttals": [],
        "timing": {"action": "WAIT"},
        "conviction": 0.5,
        "what_would_change_my_mind": [],
    }


def test_load_turns_groups_by_round_and_side(tmp_path):
    for rnd, side in ((0, "bull"), (0, "bear"), (1, "bull")):
        write_checkpoint(
            tmp_path, f"20_debate_r{rnd}_{side}", checkpoint_payload(_turn(side, rnd))
        )
    write_checkpoint(tmp_path, "10_analyst_technical", checkpoint_payload({"x": 1}))
    turns = load_turns(tmp_path)
    assert sorted(turns) == [0, 1]
    assert sorted(turns[0]) == ["bear", "bull"] and list(turns[1]) == ["bull"]
    assert turns[0]["bear"].stance == "BEARISH"
    # 다른 이름 규약도 패턴으로 받는다
    write_checkpoint(tmp_path, "20_market_r0_bear", checkpoint_payload(_turn("bear", 0)))
    other = load_turns(tmp_path, re.compile(r"^20_market_r(\d+)_(bull|bear)$"))
    assert list(other) == [0] and list(other[0]) == ["bear"]


def test_call_meta_rejects_unknown_fields():
    assert call_meta(None, None) is None
    out = call_meta("trader", {"model": "m", "cost_usd": 0.1, "bogus": 1})
    assert out == {
        "agent": "trader",
        "model": "m",
        "session_id": None,
        "cost_usd": 0.1,
        "duration_ms": None,
    }
    assert call_meta(None, {"model": "m"})["agent"] == "unknown"
    with pytest.raises(ValidationError):
        CallMeta.model_validate({"agent": "a", "bogus": 1})
