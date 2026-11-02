from trader.kernel.cli.boot import boot
from trader.kernel.registry.contracts import all_migrations


def test_boot_ports_has_accounts_and_inputs(project):
    b = boot(ports=True)
    try:
        assert {"accounts", "inputs"} <= set(b.ports)
        assert b.ports["accounts"].accounts() == []
    finally:
        b.conn.close()


def test_spot_is_lane_with_v6():
    from trader.features.spot.feature import FEATURE

    assert FEATURE.tier == "lane" and [m.version for m in FEATURE.migrations] == [6, 9]
    assert [m.owner for m in FEATURE.migrations] == [
        "spot",
        "spot",
    ]  # accounts 와 같은 기능 이름 규약
    assert [k.retry_guard for k in FEATURE.session_kinds] == [True]  # analyze 반복 타임아웃 차단
    assert [m.version for m in all_migrations([FEATURE])][-1] == 9
