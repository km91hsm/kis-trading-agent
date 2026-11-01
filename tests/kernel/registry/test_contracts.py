from dataclasses import replace
from pathlib import Path

from trader.kernel.ledger.migrations import Migration
from trader.kernel.registry import contracts
from trader.kernel.registry.discover import discover
from trader.kernel.registry.types import Feature, OriginSpec, QueueKind, SseKey

FIXTURES = Path(__file__).parents[2] / "fixtures"
OK_ROOT = FIXTURES / "features_ok"


def _ok() -> list[Feature]:
    return discover("tests.fixtures.features_ok")


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
        alpha, migrations=(Migration(2, "a2", owner="alpha"), Migration(4, "a4", owner="alpha"))
    )
    beta = replace(beta, migrations=(Migration(3, "b3", owner="beta"),))
    msgs = contracts.check([alpha, beta], roots=[OK_ROOT])
    assert not any("마이그레이션" in m for m in msgs)
    assert [m.version for m in contracts.all_migrations([alpha, beta])] == [1, 2, 3, 4]


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
