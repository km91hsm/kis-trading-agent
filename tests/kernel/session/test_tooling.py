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
    assert out["truncation"]["evidence_dropped"] is True
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
        write_checkpoint(tmp_path, f"20_debate_r{rnd}_{side}", checkpoint_payload(_turn(side, rnd)))
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
    assert {k: out[k] for k in ("agent", "model", "session_id", "cost_usd", "duration_ms")} == {
        "agent": "trader",
        "model": "m",
        "session_id": None,
        "cost_usd": 0.1,
        "duration_ms": None,
    }
    assert "bogus" not in out
    assert call_meta(None, {"model": "m"})["agent"] == "unknown"
    with pytest.raises(ValidationError):
        CallMeta.model_validate({"agent": "a", "bogus": 1})


def test_shrink_preserves_newest_news_head_and_candle_tail():
    obj = {
        "news": [{"title": f"news-{i}", "summary": "가" * 160} for i in range(200)],
        "technical": {"daily": {"data": [{"date": str(i), "close": "123"} for i in range(200)]}},
        "data_gaps": ["partial coverage"],
        "source": "KIS",
        "stale": True,
    }
    out = shrink(obj, 24000)
    assert _size(out) <= 24000
    assert out["news"][0]["title"] == "news-0"
    assert out["technical"]["daily"]["data"][-1]["date"] == "199"
    assert out["data_gaps"] == ["partial coverage"]
    assert out["source"] == "KIS" and out["stale"] is True
    assert out["truncation"]["truncated"] is True


def test_shrink_bounds_large_nested_objects_and_scalar_lists():
    obj = {
        "meta": {"market": "KR", "asof": "2026-11-07"},
        "source": "FRED",
        "data_gaps": ["monthly observation"],
        "payload": {str(i): "한글" * 1000 for i in range(500)},
        "values": ["한글" * 1000 for _ in range(500)],
    }
    out = shrink(obj, 24000)
    assert _size(out) <= 24000
    assert out["meta"] == obj["meta"] and out["source"] == "FRED"
    assert out["data_gaps"] == ["monthly observation"]
    assert out["truncation"]["truncated"] is True


def test_compact_context_delivers_news_macro_quality_without_accounts():
    from trader.kernel.session.tooling import compact_context

    inputs = {
        "meta": {"ticker": "005930", "market": "KR", "asof": "2026-11-07"},
        "market_activity": {
            "source": "kis",
            "stale": True,
            "source_asof": None,
            "rankings": {"turnover": [{"turnover": "100"}]},
        },
        "data_gaps": ["partial"],
        "news_macro": {
            "news": {
                "data": {"stock": [{"title": "최신 뉴스", "source": "news"}]},
                "received_at": "2026-11-07",
                "error": None,
            },
            "macro": {
                "vix": {
                    "data": {"value": 20},
                    "source": "FRED",
                    "source_asof": "2026-11-06",
                    "stale": None,
                }
            },
        },
        "accounts": [{"id": "private"}],
        "portfolio": {"nav": "private"},
    }
    out = compact_context(inputs)
    assert out["news"]["data"]["stock"][0]["title"] == "최신 뉴스"
    assert out["macro"]["vix"]["source"] == "FRED"
    assert out["market_activity"]["stale"] is True
    assert out["data_gaps"] == ["partial"]
    assert "accounts" not in out and "portfolio" not in out
    assert _size(out) <= 8000


def test_compact_context_preserves_blog_reference_and_source_coverage():
    from trader.kernel.session.tooling import compact_context

    inputs = {
        "source_coverage": {"macro": {"dxy": {"status": "missing"}}},
        "sentiment": {
            "community": {
                "data": {
                    "posts": [
                        {"source": "naver_blog/ranto28", "title": "시장 의견", "body": "경제 설명"},
                        {"source": "reddit", "title": "별도 커뮤니티"},
                    ]
                }
            }
        },
    }
    out = compact_context(inputs)
    assert out["source_coverage"]["macro"]["dxy"]["status"] == "missing"
    assert [p["source"] for p in out["blog_posts"]] == ["naver_blog/ranto28"]
