from dataclasses import replace
from pathlib import Path

from trader.kernel.ledger.migrations import Migration
from trader.kernel.registry import contracts
from trader.kernel.registry.discover import discover
from trader.kernel.registry.types import Feature, OriginSpec, QueueKind, SseKey

FIXTURES = Path(__file__).parents[2] / "fixtures"
OK_ROOT = FIXTURES / "features_ok"


def _ok() -> list[Feature]:
    # alpha 가 v2·v3·v6·v9 자리를 채워 커널 모듈(v4·v5·v7·v8)과 버전이 연속되게 한다.
    alpha, beta = discover("tests.fixtures.features_ok")
    stubs = tuple(Migration(v, f"a{v}", owner="alpha") for v in (2, 3, 6, 9))
    return [replace(alpha, migrations=stubs), beta]


def test_clean_fixture_passes():
    assert contracts.check(_ok(), roots=[OK_ROOT]) == []


def test_contracts_reject_duplicate_feature_ids():
    alpha, _ = _ok()
    msgs = contracts.check([alpha, alpha], roots=[OK_ROOT])
    assert any("기능 id" in m and "alpha" in m for m in msgs)


def test_contracts_reject_duplicate_job_ids():
    alpha, beta = _ok()
    beta = replace(beta, jobs=alpha.jobs)
    msgs = contracts.check([alpha, beta], roots=[OK_ROOT])
    assert any("잡 id" in m and "alpha.tick" in m for m in msgs)


def test_contracts_reject_duplicate_queue_kind_sse_key_origin():
    alpha, beta = _ok()
    qk = QueueKind(
        kind="k",
        params_model=dict,
        label="k",
        lane="slow",
        priority=1,
        trading=False,
        restart_requeue=False,
        body=lambda p: None,
    )
    origin = OriginSpec(
        origin="llm",
        risk_stage="r",
        order_stage="o",
        has_decision_row=True,
        gate_exemptions=frozenset(),
        idem="decision",
    )
    alpha = replace(alpha, queue_kinds=(qk,), origins=(origin,))
    beta = replace(
        beta,
        queue_kinds=(qk,),
        origins=(origin,),
        sse_keys=(SseKey(key="alpha", probe=lambda c: 0),),
    )
    msgs = contracts.check([alpha, beta], roots=[OK_ROOT])
    assert any("큐 kind" in m for m in msgs)
    assert any("SSE 키" in m for m in msgs)
    assert any("origin" in m for m in msgs)


def test_contracts_reject_duplicate_migration_version():
    alpha, beta = _ok()
    alpha = replace(alpha, migrations=(Migration(2, "a", owner="alpha"),))
    beta = replace(beta, migrations=(Migration(2, "b", owner="beta"),))
    msgs = contracts.check([alpha, beta], roots=[OK_ROOT])
    assert any("마이그레이션" in m and "2" in m for m in msgs)


def test_interleaved_migration_versions_across_features_are_accepted():
    alpha, beta = _ok()
    alpha = replace(
        alpha,
        migrations=tuple(Migration(v, f"a{v}", owner="alpha") for v in (2, 6, 9)),
    )
    beta = replace(beta, migrations=(Migration(3, "b3", owner="beta"),))
    msgs = contracts.check([alpha, beta], roots=[OK_ROOT])
    assert not any("마이그레이션" in m for m in msgs)
    assert [m.version for m in contracts.all_migrations([alpha, beta])] == list(range(1, 10))


def test_duplicate_version_still_reported_with_interleaving():
    alpha, beta = _ok()
    alpha = replace(
        alpha, migrations=(Migration(2, "a2", owner="alpha"), Migration(3, "a3", owner="alpha"))
    )
    beta = replace(beta, migrations=(Migration(3, "b3", owner="beta"),))
    msgs = contracts.check([alpha, beta], roots=[OK_ROOT])
    assert any("마이그레이션" in m and "3" in m for m in msgs)


def test_contracts_require_readme(tmp_path):
    (tmp_path / "alpha").mkdir()
    (tmp_path / "beta").mkdir()
    (tmp_path / "beta" / "README.md").write_text("# beta\n", encoding="utf-8")
    msgs = contracts.check(_ok(), roots=[tmp_path])
    assert any("README.md" in m and "alpha" in m for m in msgs)
    assert not any("README.md" in m and "beta" in m for m in msgs)


def test_real_tree_has_no_violations():
    assert contracts.check(discover()) == []


def test_duplicate_agent_names_reported():
    from trader.kernel.registry.types import AgentDef

    alpha, beta = _ok()
    a = AgentDef(name="trader", path=Path("a.md"))
    msgs = contracts.check(
        [replace(alpha, agents=(a,)), replace(beta, agents=(a,))], roots=[OK_ROOT]
    )
    assert "에이전트 이름 중복: trader" in msgs


def test_duplicate_port_names_reported():
    from trader.kernel.registry.types import Port

    alpha, beta = _ok()
    p = Port(name="broker", build=lambda b: None)
    msgs = contracts.check([replace(alpha, ports=(p,)), replace(beta, ports=(p,))], roots=[OK_ROOT])
    assert "포트 중복: broker" in msgs


def test_lane_tier_must_match_lanes_both_ways():
    alpha, _ = _ok()
    msgs = contracts.check([replace(alpha, tier="lane")], roots=[OK_ROOT])
    assert "기능 alpha: tier=lane 이지만 LANES 에 없음" in msgs
    msgs = contracts.check([replace(alpha, id="spot", tier="service")], roots=[OK_ROOT])
    assert "LANES 의 spot 가 tier=lane 이 아님" in msgs
    # 기능이 없으면 LANES 항목은 검사하지 않는다
    assert not any("LANES" in m for m in contracts.check([alpha], roots=[OK_ROOT]))


def test_origin_outside_check_reported():
    alpha, _ = _ok()
    o = OriginSpec("bogus", "r", "o", False, frozenset(), "decision")
    msgs = contracts.check([replace(alpha, origins=(o,))], roots=[OK_ROOT])
    assert "기능 alpha: origin bogus 는 orders.origin CHECK 밖" in msgs
    ok = replace(o, origin="llm")
    assert contracts.check([replace(alpha, origins=(ok,))], roots=[OK_ROOT]) == []


def test_stage_rule_defaults():
    from trader.kernel.registry.types import StageRule, ToolCommand

    r = StageRule(pattern="x")
    assert r.requires == () and r.min_counts == ()
    assert ToolCommand(name="t", handler=print).stage_rules == ()
