"""StageRule 표(WRITE_RULES·SUBMIT_RULE)와 TRADER_DRY_RUN 해석 테스트."""

import pytest

from trader.features.spot import tools
from trader.features.spot.feature import FEATURE
from trader.features.spot.tool_cli import SUBMIT_RULE, WRITE_RULES
from trader.kernel.cli.boot import boot
from trader.kernel.session.stages import check_rule, first_rule


def test_stage_rules_cover_write_and_submit():
    """LLM 쓰기 단계는 애널리스트·토론 턴뿐이고 30_decision 은 제출 전용 규칙만 받는다."""
    for stage in (
        "10_analyst_technical",
        "10_analyst_news_macro",
        "20_debate_r0_bull",
        "20_debatex_r0_bull",
    ):
        assert first_rule(WRITE_RULES, stage) is not None, stage
    for stage in ("30_decision", "25_research_summary", "00_inputs", "40_risk_check_a"):
        assert first_rule(WRITE_RULES, stage) is None, stage
    assert check_rule(SUBMIT_RULE, "30_decision", ["00_inputs"]) != []
    assert check_rule(SUBMIT_RULE, "20_debate_r0_bull", []) != []  # 패턴 밖 단계는 거부
    # 선언 표: checkpoint 그룹이 쓰기 규칙을 싣는다
    assert {t.name: t.stage_rules for t in FEATURE.tools}["checkpoint"] == WRITE_RULES


def test_submit_rule_requires_two_reports_and_summary():
    base = ["00_inputs", "10_analyst_technical"]
    msgs = check_rule(SUBMIT_RULE, "30_decision", base)
    assert any("25_research_summary" in m for m in msgs)
    assert any("10_analyst_*" in m and "2개" in m for m in msgs)
    ok = [*base, "10_analyst_fundamental", "25_research_summary"]
    assert check_rule(SUBMIT_RULE, "30_decision", ok) == []
    # 토론 턴 쓰기는 애널리스트 2개 이상이 선행
    debate = first_rule(WRITE_RULES, "20_debate_r1_bear")
    assert check_rule(debate, "20_debate_r1_bear", ["00_inputs", "10_analyst_technical"]) != []
    assert check_rule(debate, "20_debate_r1_bear", ["00_inputs", *ok]) == []


@pytest.mark.parametrize(
    ("env", "expected"), [(None, True), ("1", True), ("0", False), ("", True)]
)
def test_dry_run_env_missing_means_dry_run(project, monkeypatch, env, expected):
    """Review Focus 1: TRADER_DRY_RUN 이 없거나 "1" 이면 dry_run, "0" 일 때만 실주문 가능."""
    if env is None:
        monkeypatch.delenv("TRADER_DRY_RUN", raising=False)
    else:
        monkeypatch.setenv("TRADER_DRY_RUN", env)
    b = boot(ports=True)
    try:
        ctx = tools.tool_ctx(b)
    finally:
        b.conn.close()
    assert ctx.dry_run is expected
    assert ctx.policy is not None and ctx.paused is not None and ctx.inputs is not None


def test_submit_decision_cli_without_dry_run_env_sends_nothing(project, monkeypatch):
    """Review Focus 1 끝까지: `TRADER_DRY_RUN` 이 없으면 `trader tool submit-decision` 은 브로커가
    있어도 주문·승인 요청을 하나도 만들지 않는다(auto·approve 계좌 모두)."""
    import json

    import yaml
    from typer.testing import CliRunner

    from tests.features.spot.helpers import ok_decision
    from tests.features.spot.test_cli_analyze import _inject_fake_broker
    from tests.features.spot.test_tools import _ready_for_submit
    from trader.features.accounts.ports import AccountsPortImpl
    from trader.kernel.cli.main import app

    monkeypatch.delenv("TRADER_DRY_RUN", raising=False)
    (project / "config" / "accounts.yaml").write_text(
        yaml.safe_dump(
            {
                "accounts": [
                    {"id": "paper-auto", "env": "paper", "cano": "00000000",
                     "markets": ["KR"], "mode": "auto"},
                    {"id": "paper-appr", "env": "paper", "cano": "00000000",
                     "markets": ["KR"], "mode": "approve"},
                ]
            }
        ),
        encoding="utf-8",
    )  # fmt: skip
    _inject_fake_broker(monkeypatch)
    made: list = []
    real_brokers = AccountsPortImpl.brokers

    def tracked(self):
        out = real_brokers(self)
        made.extend(out)
        return out

    monkeypatch.setattr(AccountsPortImpl, "brokers", tracked)
    b = boot(ports=True)
    try:
        _ready_for_submit(tools.tool_ctx(b), project)
    finally:
        b.conn.close()
    args = ["tool", "submit-decision", "--ticker", "005930", "--market", "KR",
            "--date", "2026-10-09", "--json", json.dumps(ok_decision())]  # fmt: skip
    r = CliRunner().invoke(app, args)
    assert r.exit_code == 0, r.output
    out = json.loads(r.stdout)
    assert out["ok"] is True and {e["account_id"] for e in out["executions"]} == {
        "paper-auto", "paper-appr",
    }  # fmt: skip
    assert made and all(br.orders == [] for br in made)
    b = boot(conn=True)
    try:
        assert b.conn.execute("SELECT COUNT(*) FROM approvals").fetchone()[0] == 0
    finally:
        b.conn.close()
