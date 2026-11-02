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

    assert FEATURE.tier == "lane" and [m.version for m in FEATURE.migrations] == [6]
    assert [m.version for m in all_migrations([FEATURE])][-1] == 6
