"""정상 리포트·상호 반박의 근거 식별자는 양 provider에서 압축 후에도 모두 남는다."""

import json
import subprocess
from datetime import UTC, date, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from tests.features.spot.helpers import ok_report, ok_turn
from tests.kernel.session.test_research_policy import RESEARCHES
from trader.kernel.config import LlmSettings
from trader.kernel.session import research_policy as policy
from trader.kernel.session import tooling
from trader.kernel.session.run_store import (
    checkpoint_payload,
    market_dir,
    run_dir,
    write_checkpoint,
)
from trader.kernel.session.schemas import AnalystReport, DebateTurn

NOW = datetime(2026, 11, 7, tzinfo=UTC)
DAY = date(2026, 11, 7)
SOURCE = "technical.indicators." + "s" * 150


def turn(side, rnd):
    other = "bull" if side == "bear" else "bear"

    def issue(owner, i):
        return f"{owner}:case{i}"

    return DebateTurn.model_validate(
        ok_turn(
            side,
            rnd,
            issues=[
                {
                    "issue_id": issue(side, i),
                    "topic": "독립 쟁점",
                    "claim": "가" * 320,
                    "evidence": [SOURCE],
                }
                for i in range(3)
            ],
            responses=[
                {"issue_id": issue(other, i), "response": "나" * 320, "evidence": [SOURCE]}
                for i in range(3)
            ]
            if rnd
            else [],
            replies=[
                {"issue_id": issue(side, i), "response": "다" * 320, "evidence": [SOURCE]}
                for i in range(3)
            ]
            if rnd > 1
            else [],
            rebuttals=["상호 반박"] if rnd else [],
        )
    ).model_dump()


def assert_contract(data, rnd, side, count):
    assert len(json.dumps(data, ensure_ascii=False).encode()) <= 24_000
    assert len(data["reports"]) == count
    for report in data["reports"]:
        assert [e["source_id"] for e in report["evidence"]] == [SOURCE]
    for key, owner in (
        ("own_previous", side),
        ("opponent_previous", "bull" if side == "bear" else "bear"),
    ):
        if rnd == 0:
            assert data[key] is None
            continue
        saved = data[key]
        assert [i["issue_id"] for i in saved["issues"]] == [f"{owner}:case{i}" for i in range(3)]
        assert all(i["evidence"] == [SOURCE] for i in saved["issues"])
        if rnd > 1:
            assert len(saved["responses"]) == 3
            assert all(i["evidence"] == [SOURCE] for i in saved["responses"])
        if rnd > 2:
            assert [i["issue_id"] for i in saved["replies"]] == [
                f"{owner}:case{i}" for i in range(3)
            ]
            assert all(i["evidence"] == [SOURCE] for i in saved["replies"])


@pytest.mark.parametrize("scope", ["spot", "fo", "market"])
def test_large_reports_complete_both_provider_rounds_with_immutable_policy(
    conn, tmp_path, monkeypatch, scope
):
    from trader.features.fo import tools as fo
    from trader.features.regime import tools as market
    from trader.features.spot import tools as spot

    monkeypatch.delenv("FAKE_CLAUDE", raising=False)
    settings = SimpleNamespace(llm=LlmSettings(codex_debate={"enabled": True}), debate_rounds=3)
    d = (
        run_dir(DAY, "KR", "005930", tmp_path)
        if scope == "spot"
        else market_dir(DAY, "KR", tmp_path, create=True)
    )
    spec = RESEARCHES[scope]
    frozen = policy.start(conn, settings, d, spec, now=NOW, version=1)["policy_hash"]
    inputs = {
        "meta": {"market": "KR", "ticker": "005930", "asof": NOW.isoformat()},
        "market": "KR",
        "proxy": "005930",
        "asof": NOW.isoformat(),
    }
    write_checkpoint(d, spec.input_stage, inputs if scope == "spot" else checkpoint_payload(inputs))
    roles = (
        ["technical", "fundamental", "news_macro", "sentiment"]
        if scope != "market"
        else ["technical"]
    )
    if scope == "fo":
        roles.remove("fundamental")
    for role in roles:
        report = AnalystReport.model_validate(
            ok_report(
                role=role,
                bullets=["가" * 256] * 7,
                evidence=[{"claim": "원천 근거", "source_id": SOURCE}],
            )
        ).model_dump()
        write_checkpoint(
            d,
            spec.report_prefix + role if scope != "market" else spec.report_prefix,
            checkpoint_payload(report),
        )
    ctx = SimpleNamespace(conn=conn, settings=settings, now=lambda: NOW)

    def fake(cmd, **kw):
        data = json.loads(kw["input"].split("\n", 1)[1])
        assert_contract(data, data["round"], "bear", len(roles))
        if scope != "market" and data["round"]:
            assert data["truncation"]["truncated"]
        Path(cmd[cmd.index("--output-last-message") + 1]).write_text(
            json.dumps(turn("bear", data["round"])), encoding="utf-8"
        )
        return subprocess.CompletedProcess(cmd, 0, "", "")

    for rnd in range(4):
        if scope == "spot":
            bundle = spot.checkpoint_bundle(
                spot.ToolContext(settings=settings, accounts=[], conn=conn, state=tmp_path),
                ticker="005930",
                market="KR",
                date=DAY,
                kind="researcher",
                round=rnd,
                side="bull",
            )
        elif scope == "fo":
            bundle = fo.fo_bundle(
                fo.FoToolContext(
                    settings=settings, conn=conn, state=tmp_path, fo=SimpleNamespace()
                ),
                date=DAY,
                kind="researcher",
                round=rnd,
                side="bull",
            )
        else:
            bundle = market.market_bundle(
                market.MarketToolContext(settings=settings, conn=conn, state=tmp_path),
                market="KR",
                date=DAY,
                kind="researcher",
                round=rnd,
                side="bull",
            )
        assert bundle["ok"], bundle
        assert_contract(bundle["data"], rnd, "bull", len(roles))
        bull = turn("bull", rnd)
        assert policy.turn_error(conn, settings, d, spec, bull) is None
        write_checkpoint(
            d, f"{spec.turn_prefix}{rnd}_bull", checkpoint_payload(bull, {"provider": "claude"})
        )
        result = policy.generate_turn(ctx, d, spec, rnd, runner=fake, bin_path=Path("/fake/codex"))
        assert result["ok"], result
    assert policy.get(conn, settings, d, spec)["policy_hash"] == frozen
    assert policy.completion_error(conn, settings, d, spec) is None


def test_oversized_required_evidence_identifiers_are_rejected_without_launch(conn, monkeypatch):
    monkeypatch.delenv("FAKE_CLAUDE", raising=False)
    data = {
        "scope": "spot",
        "market": "KR",
        "ticker": "005930",
        "currency": "KRW",
        "asof": NOW.isoformat(),
        "round": 0,
        "side": "bear",
        "reports": [{"evidence": [{"source_id": "s" * 25000, "claim": "가"}]}],
    }
    with pytest.raises(ValueError, match="24,000"):
        tooling.compact_debate(data)
    assert conn.execute("SELECT COUNT(*) FROM research_calls").fetchone()[0] == 0


def test_compaction_rejects_when_meaningful_evidence_excerpts_cannot_fit():
    data = {
        "reports": [
            {"evidence": [{"source_id": f"source:{i}", "claim": "검" * 600} for i in range(150)]}
        ],
        "own_previous": turn("bear", 2),
        "opponent_previous": turn("bull", 2),
    }
    with pytest.raises(ValueError, match="24,000"):
        tooling.compact_debate(data)
