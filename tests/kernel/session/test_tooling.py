"""kernel/session/tooling — 체크포인트 도구 헬퍼."""

import json
import re

import pytest
from pydantic import ValidationError

from trader.kernel.session import tooling
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


def test_debate_re_recognises_both_lanes():
    p = tooling.debate_re("20_debate_r", "20_debatex_r")
    assert tooling.parse_debate_stage(p, "20_debate_r0_bull") == ("a", 0, "bull")
    assert tooling.parse_debate_stage(p, "20_debatex_r2_bear") == ("b", 2, "bear")
    assert tooling.parse_debate_stage(p, "20_debatey_r0_bull") is None
    assert tooling.parse_debate_stage(p, "20_debate_r0_bull.json") is None


def test_load_lanes_splits_by_lane(tmp_path):
    for stage in ("20_debate_r0_bull", "20_debatex_r0_bull"):
        write_checkpoint(tmp_path, stage, checkpoint_payload(_turn("bull", 0)))
    lanes = tooling.load_lanes(tmp_path, tooling.DEBATE_RE)
    assert set(lanes) == {"a", "b"} and set(lanes["b"][0]) == {"bull"}
    assert set(tooling.load_turns(tmp_path)) == {0}  # 기본 lane="a"
    assert set(tooling.load_turns(tmp_path, lane="b")) == {0}
    assert tooling.load_lanes(tmp_path / "none", tooling.DEBATE_RE) == {}


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


def test_compact_context_keeps_each_macro_and_blog_evidence_under_realistic_load():
    from trader.kernel.session.tooling import compact_context

    macro = {
        key: {
            "data": {"value": i + 10, "date": "2026-11-06"},
            "source": "FRED",
            "source_asof": "2026-11-06",
            "received_at": "2026-11-07T01:00:00Z",
            "status": "available",
            "stale": None,
        }
        for i, key in enumerate(("sp500", "nasdaq", "wti", "dxy", "us10y"))
    }
    coverage = {
        key: {
            "status": "present",
            "source": "FRED",
            "series_id": series,
            "instrument": instrument,
            "source_asof": "2026-11-06",
            "received_at": "2026-11-07T01:00:00Z",
            "stale": None,
            "data_gaps": ["Investing.com source not collected; FRED series used"],
        }
        for key, series, instrument in (
            ("sp500", "SP500", "S&P 500"),
            ("nasdaq", "NASDAQCOM", "NASDAQ Composite"),
            ("wti", "DCOILWTICO", "WTI crude oil spot price"),
            ("dxy", "DTWEXBGS", "Federal Reserve broad US dollar index"),
            ("us10y", "DGS10", "US Treasury 10-year constant maturity yield"),
        )
    }
    inputs = {
        "meta": {"market": "KR", "asof": "2026-11-07T01:00:00Z"},
        "market_activity": {
            "source": "kis",
            "market": "KR",
            "status": "partial",
            "rankings": {"turnover": [{"ticker": "005930", "turnover": "100"}]},
        },
        "data_gaps": ["partial activity coverage"],
        "source_coverage": {
            "macro": coverage,
            "naver_blog/ranto28": {
                "status": "present",
                "items": 5,
                "source": "Naver blog RSS",
                "data_gaps": [],
            },
        },
        "news_macro": {
            "news": {
                "data": {
                    "stock": [
                        {"title": f"news-{i}", "summary": "가" * 360, "source": "news"}
                        for i in range(5)
                    ]
                }
            },
            "macro": macro,
            "events": {"recent": [], "upcoming": []},
        },
        "sentiment": {
            "saveticker": {"data": {"source": "saveticker", "stale": ["market_gain"]}},
            "community": {
                "data": {
                    "posts": [
                        {
                            "title": f"blog-{i}",
                            "body": "나" * 1800,
                            "url": f"https://example.test/posts/{i}?label=" + "경제" * 25,
                            "source": "naver_blog/ranto28",
                            "posted_at": "2026-11-06T01:00:00Z",
                            "received_at": "2026-11-07T01:00:00Z",
                        }
                        for i in range(5)
                    ]
                }
            },
        },
    }
    out = compact_context(inputs)
    assert _size(out) <= 8000
    assert len(out["blog_posts"]) == 5
    for i, post in enumerate(out["blog_posts"]):
        assert len(post["body"].strip(" …")) >= 63
        assert post["url"] == f"https://example.test/posts/{i}?label=" + "경제" * 25
        assert post["source"] == "naver_blog/ranto28"
        assert post["posted_at"] == "2026-11-06T01:00:00Z"
        assert post["received_at"] == "2026-11-07T01:00:00Z"
    assert inputs["sentiment"]["community"]["data"]["posts"][0]["body"] == "나" * 1800
    assert out["blog_posts"][0]["body"]
    assert out["blog_posts"][0]["posted_at"] == "2026-11-06T01:00:00Z"
    assert out["news"]["data"]["stock"][0]["title"] == "news-0"
    assert set(out["source_coverage"]["macro"]) == {"sp500", "nasdaq", "wti", "dxy", "us10y"}
    assert out["source_coverage"]["macro"]["dxy"]["series_id"] == "DTWEXBGS"
    assert (
        out["source_coverage"]["macro"]["us10y"]["instrument"]
        == "US Treasury 10-year constant maturity yield"
    )
    for key in ("sp500", "nasdaq", "wti", "dxy", "us10y"):
        assert out["macro"][key]["data"]["date"] == "2026-11-06"
        assert isinstance(out["macro"][key]["data"]["value"], int)
    assert "events" in out and "saveticker" in out
    blog_coverage = out["source_coverage"]["naver_blog/ranto28"]
    assert blog_coverage["items"] == len(out["blog_posts"])
    assert blog_coverage["status"] == "present"
    if len(out["blog_posts"]) < 5:
        assert blog_coverage["status"] == "partial"
        assert blog_coverage["data_gaps"]


def test_compact_context_marks_coverage_dropped_when_referenced_evidence_is_missing():
    from trader.kernel.session.tooling import compact_context

    out = compact_context(
        {
            "source_coverage": {
                "macro": {"dxy": {"status": "present", "series_id": "DTWEXBGS", "data_gaps": []}},
                "naver_blog/ranto28": {"status": "present", "items": 5, "data_gaps": []},
            }
        }
    )
    assert out["source_coverage"]["macro"]["dxy"]["status"] == "dropped"
    assert out["source_coverage"]["macro"]["dxy"]["data_gaps"]
    blog = out["source_coverage"]["naver_blog/ranto28"]
    assert blog["status"] == "dropped" and blog["items"] == 0 and blog["original_items"] == 5
    assert blog["data_gaps"]


def test_compact_context_does_not_count_ellipsis_as_delivered_blog_evidence():
    from trader.kernel.session.tooling import compact_context

    out = compact_context(
        {
            "source_coverage": {
                "naver_blog/ranto28": {"status": "present", "items": 1, "data_gaps": []}
            },
            "blog_posts": [{"source": "naver_blog/ranto28", "body": "…"}],
        }
    )
    assert out["source_coverage"]["naver_blog/ranto28"]["status"] == "dropped"
    assert out["source_coverage"]["naver_blog/ranto28"]["items"] == 0


@pytest.mark.parametrize("limit", [8000, 24000])
def test_shrink_keeps_bounded_fallback_reason_and_daily_source_metadata(limit):
    reason = "KIS upstream unavailable: " + "합성 진단 " * 10000
    obj = {
        "macro": {"sp500": {"data": {"value": 4300, "date": "2026-11-05"}}},
        "source_coverage": {
            "macro": {
                "sp500": {
                    "status": "present",
                    "source": "FRED",
                    "series_id": "SP500",
                    "instrument": "S&P 500",
                    "fallback_reason": reason,
                    "unit": "index points",
                    "observed_date": "2026-11-05",
                    "timestamp_precision": "day",
                    "session": "daily_observation",
                }
            }
        },
        "news": [{"title": "합성 뉴스", "summary": "가" * 2000} for _ in range(20)],
    }
    out = shrink(obj, limit)
    assert _size(out) <= limit
    coverage = out["source_coverage"]["macro"]["sp500"]
    assert coverage["status"] == "present"
    assert coverage["fallback_reason"].startswith("KIS upstream unavailable: ")
    assert len(coverage["fallback_reason"]) <= 301
    assert coverage["unit"] == "index points"
    assert coverage["observed_date"] == "2026-11-05"
    assert coverage["timestamp_precision"] == "day"
    assert coverage["session"] == "daily_observation"


def test_compact_context_preserves_blog_links_when_only_partial_posts_fit():
    from trader.kernel.session.tooling import compact_context

    posts = [
        {"title": f"글 {i}", "body": "합성 근거 문장 " * 300,
         "url": f"https://example.test/{i}?query=" + "x" * 7500,
         "source": "naver_blog/ranto28", "posted_at": "2026-11-06T01:00:00Z",
         "received_at": "2026-11-07T01:00:00Z"}
        for i in range(5)
    ]
    out = compact_context({"blog_posts": posts, "source_coverage": {
        "naver_blog/ranto28": {"status": "present", "items": 5, "data_gaps": []}}})
    assert _size(out) <= 8000
    assert len(out.get("blog_posts", [])) <= 1
    for post in out.get("blog_posts", []):
        assert post["url"] in {p["url"] for p in posts}
    coverage = out["source_coverage"]["naver_blog/ranto28"]
    assert coverage["status"] in {"partial", "dropped"}
    assert coverage["original_items"] == 5
    assert coverage["items"] < 5
    assert coverage["data_gaps"]


def test_compact_context_repeated_excerpts_keep_all_five_posts_and_truncation():
    from trader.kernel.session.tooling import compact_context

    inputs = {"blog_posts": [
        {"title": f"합성 글 {i}", "body": "각 글의 서로 다른 근거 " * 200,
         "url": f"https://example.test/{i}", "source": "naver_blog/ranto28",
         "posted_at": "2026-11-06T01:00:00Z", "received_at": "2026-11-07T01:00:00Z"}
        for i in range(5)], "source_coverage": {
        "naver_blog/ranto28": {"status": "present", "items": 5, "data_gaps": []}}}
    first = compact_context(inputs)
    second = compact_context(first)
    assert len(first["blog_posts"]) == len(second["blog_posts"]) == 5
    assert second["blog_posts"] == first["blog_posts"]
    assert second["truncation"] == first["truncation"]
    assert second["source_coverage"]["naver_blog/ranto28"]["status"] == "present"
    assert _size(second) <= 8000


def test_compact_context_counts_only_nonempty_excerpts_when_many_posts_are_trimmed():
    from trader.kernel.session.tooling import compact_context

    posts = [
        {"source": "naver_blog/ranto28", "url": f"https://example.test/{i}",
         "body": "" if i == 0 else "합성 자료 " * 1000}
        for i in range(12)
    ]
    out = compact_context({"blog_posts": posts, "source_coverage": {
        "naver_blog/ranto28": {"status": "present", "items": 12, "data_gaps": []}}})
    assert len(out["blog_posts"]) == 5
    coverage = out["source_coverage"]["naver_blog/ranto28"]
    assert coverage["status"] == "partial"
    assert coverage["items"] == 4 and coverage["original_items"] == 12
    assert out["blog_posts"][0]["body"] == ""
    assert _size(out) <= 8000


def test_compact_context_skips_one_oversized_blog_without_losing_other_posts():
    from trader.kernel.session.tooling import compact_context

    for oversized_index in (0, 2, 4):
        posts = [
            {"source": "naver_blog/ranto28", "title": f"합성 글 {i}",
             "url": "https://example.test/" + ("x" * 9000 if i == oversized_index else str(i)),
             "body": "근거 발췌 " * 100, "posted_at": "2026-11-06T01:00:00Z",
             "received_at": "2026-11-07T01:00:00Z"}
            for i in range(5)
        ]
        out = compact_context({"blog_posts": posts, "source_coverage": {
            "naver_blog/ranto28": {"status": "present", "items": 5, "data_gaps": []}},
            "macro": {"sp500": {"data": {"value": "6500"}, "source": "KIS"}}})
        assert [p["url"] for p in out["blog_posts"]] == [
            p["url"] for i, p in enumerate(posts) if i != oversized_index
        ]
        assert all(p["body"].strip(" …") for p in out["blog_posts"])
        assert out["macro"]["sp500"]["data"]["value"] == "6500"
        coverage = out["source_coverage"]["naver_blog/ranto28"]
        assert coverage["status"] == "partial"
        assert coverage["items"] == 4 and coverage["original_items"] == 5
        assert _size(out) <= 8000


def _dense_market_context(posts):
    macro = {
        key: {"data": {"value": i + 10, "date": "2026-11-06"}, "source": "FRED",
              "source_asof": "2026-11-06", "received_at": "2026-11-07T01:00:00Z"}
        for i, key in enumerate(("sp500", "nasdaq", "wti", "dxy", "us10y"))
    }
    rows = [{
        "ticker": f"{i:06d}", "name": "합성 종목", "market": "KR", "currency": "KRW",
        "exchange": "KRX", "rank": i + 1, "price": "10000", "change_pct": "1.25",
        "volume": "10000", "turnover": "100000000", "turnover_unit": "unknown",
        "turnover_basis": "unknown", "turnover_source_field": "acml_tr_pbmn",
        "source": "KIS", "source_asof": None, "received_at": "2026-11-07T01:00:00Z",
        "status": "partial", "basis": "unknown", "stale": False,
        "data_gaps": ["원천 누적 범위와 거래대금 단위 미확인; 합성 시험 자료"] * 3,
    } for i in range(10)]
    return {
        "macro": macro,
        "market_activity": {"source": "KIS", "status": "partial", "rankings": {
            key: rows for key in ("turnover", "gain", "loss")}},
        "source_coverage": {
            "macro": {key: {"status": "present"} for key in macro},
            "naver_blog/ranto28": {"status": "present", "items": len(posts), "data_gaps": []}},
        "blog_posts": posts,
    }


def test_compact_context_keeps_macro_and_rankings_after_excluding_oversized_blog():
    from trader.kernel.session.tooling import compact_context

    inputs = _dense_market_context([{"url": "https://example.test/" + "x" * 9000,
                                    "source": "naver_blog/ranto28", "body": "합성 발췌"}])
    macro = inputs["macro"]
    out = compact_context(inputs)
    assert set(out["macro"]) == set(macro)
    assert all(out["macro"][key]["data"]["value"] == macro[key]["data"]["value"] for key in macro)
    assert all(
        len(out["market_activity"]["rankings"][key]) >= 3
        for key in ("turnover", "gain", "loss")
    )
    assert not out.get("blog_posts")
    assert out["source_coverage"]["naver_blog/ranto28"]["status"] == "dropped"
    assert inputs["blog_posts"][0]["url"] == "https://example.test/" + "x" * 9000
    assert _size(out) <= 8000


def test_compact_context_preserves_five_blogs_when_rank_lists_need_further_reduction():
    from trader.kernel.session.tooling import compact_context

    inputs = _dense_market_context([
        {"source": "naver_blog/ranto28", "url": f"https://example.test/{i}",
         "body": "합성 근거 " * 100, "posted_at": "2026-11-06T01:00:00Z",
         "received_at": "2026-11-07T01:00:00Z"}
        for i in range(5)
    ])
    out = compact_context(inputs)
    assert len(out["blog_posts"]) == 5
    assert [p["url"] for p in out["blog_posts"]] == [p["url"] for p in inputs["blog_posts"]]
    assert all(p["body"].strip(" …") for p in out["blog_posts"])
    assert out["macro"] == inputs["macro"]
    assert all(out["market_activity"]["rankings"][k] for k in ("turnover", "gain", "loss"))
    assert out["source_coverage"]["naver_blog/ranto28"]["status"] == "present"
    assert _size(out) <= 8000


def _summ(long: list[str]) -> dict:
    from trader.kernel.session.debate import research_summary
    from trader.kernel.session.schemas import DebateTurn

    def t(side, rnd):
        return DebateTurn.model_validate(
            {**_turn(side, rnd), "arguments": long, "rebuttals": long,
             "what_would_change_my_mind": long}
        )  # fmt: skip

    r0 = {"bull": t("bull", 0), "bear": t("bear", 0)}
    return research_summary(r0, {"bull": t("bull", 1), "bear": t("bear", 1)}, 1)


BRIEF_KEYS = {"side", "round", "stance", "valuation_view", "timing", "conviction"}


def test_trader_summary_briefs_by_lane_turns():
    from trader.kernel.session.debate import merge_lanes

    long = ["가" * 600] * 5
    merged = merge_lanes({"a": _summ(long), "b": _summ(long)})
    out = tooling.trader_summary(merged)
    for lane in ("a", "b"):
        assert "issues" not in out["by_lane"][lane]
        for part in ("round0", "final"):
            for t in out["by_lane"][lane][part].values():
                assert set(t) <= BRIEF_KEYS
    assert len(json.dumps(out, ensure_ascii=False).encode()) < 24_000
    assert merged["by_lane"]["a"]["final"]["bull"]["arguments"] == long  # 원본은 건드리지 않는다


def _write_turn(d, stage, side, rnd):
    write_checkpoint(d, stage, checkpoint_payload(_turn(side, rnd)))


def test_summarize_turns_rejects_lane_b_without_r0(tmp_path):
    _write_turn(tmp_path, "20_debate_r0_bull", "bull", 0)
    _write_turn(tmp_path, "20_debate_r0_bear", "bear", 0)
    _write_turn(tmp_path, "20_debatex_r0_bull", "bull", 0)
    out = tooling.summarize_turns(tmp_path, "25_research_summary", tooling.DEBATE_RE)
    assert out["ok"] is False and "2판" in out["error"]
    assert "라운드 0 의 bull·bear 턴이 모두 필요합니다" in out["error"]


def test_summarize_turns_rejects_missing_lane_a(tmp_path):
    _write_turn(tmp_path, "20_debatex_r0_bull", "bull", 0)
    _write_turn(tmp_path, "20_debatex_r0_bear", "bear", 0)
    out = tooling.summarize_turns(tmp_path, "25_research_summary", tooling.DEBATE_RE)
    assert out == {"ok": False, "error": "라운드 0 의 bull·bear 턴이 모두 필요합니다"}


def test_summarize_turns_single_and_two_lanes(tmp_path):
    from trader.kernel.session.run_store import read_checkpoint

    for side in ("bull", "bear"):
        _write_turn(tmp_path, f"20_debate_r0_{side}", side, 0)
    one = tooling.summarize_turns(tmp_path, "25_research_summary", tooling.DEBATE_RE)
    assert one["ok"] is True and one["lanes"] == ["a"] and one["rounds"] == 0
    assert "by_lane" not in read_checkpoint(tmp_path, "25_research_summary")["data"]
    for side in ("bull", "bear"):
        _write_turn(tmp_path, f"20_debatex_r0_{side}", side, 0)
    two = tooling.summarize_turns(tmp_path, "25_research_summary", tooling.DEBATE_RE)
    assert two["ok"] is True and two["lanes"] == ["a", "b"]
    data = read_checkpoint(tmp_path, "25_research_summary")["data"]
    assert set(data["by_lane"]) == {"a", "b"} and data["lanes"] == ["a", "b"]


def test_summarize_turns_rejects_lanes_with_different_last_round(tmp_path):
    for stage, side, rnd in (
        ("20_debate_r0_bull", "bull", 0), ("20_debate_r0_bear", "bear", 0),
        ("20_debate_r1_bull", "bull", 1), ("20_debate_r1_bear", "bear", 1),
        ("20_debatex_r0_bull", "bull", 0), ("20_debatex_r0_bear", "bear", 0),
    ):  # fmt: skip
        _write_turn(tmp_path, stage, side, rnd)
    out = tooling.summarize_turns(tmp_path, "25_research_summary", tooling.DEBATE_RE)
    assert out == {"ok": False, "error": "두 판의 마지막 라운드가 다릅니다"}
