import pytest

from trader.kernel.cli import boot as boot_mod
from trader.kernel.config import ConfigError
from trader.kernel.ledger.migrations import Migration
from trader.kernel.registry.types import Feature, Port


def _feat(fid, *ports):
    # 첫 기능 "a" 가 v2·v3·v6·v9~v14·v16을 채워 커널 모듈(v4·v5·v7·v8·v15·v17·v18)과 연속되게 한다.
    versions = (2, 3, 6, *range(9, 15), 16, 19)
    migs = tuple(Migration(v, f"a{v}") for v in versions) if fid == "a" else ()
    return Feature(id=fid, tier="service", ports=tuple(ports), migrations=migs)


def test_boot_ports_builds_each_feature_port(project, monkeypatch):
    seen = {}

    def build_a(b):
        seen["conn"] = b.conn
        seen["settings"] = b.settings
        return "A"

    feats = [_feat("a", Port("pa", build_a)), _feat("b", Port("pb", lambda b: "B"))]
    monkeypatch.setattr(boot_mod, "discover", lambda: feats)
    monkeypatch.setattr(boot_mod, "check", lambda fs: [])
    b = boot_mod.boot(ports=True)
    try:
        assert b.ports == {"pa": "A", "pb": "B"}
        assert seen["conn"] is b.conn and b.conn is not None
        assert seen["settings"] is b.settings and b.secrets is not None
    finally:
        b.conn.close()


def test_boot_port_failure_is_config_error(project, monkeypatch):
    def bad(b):
        raise ValueError("키 없음")

    monkeypatch.setattr(boot_mod, "discover", lambda: [_feat("a", Port("pa", bad))])
    monkeypatch.setattr(boot_mod, "check", lambda fs: [])
    with pytest.raises(ConfigError, match="포트 pa 조립 실패: ValueError: 키 없음"):
        boot_mod.boot(ports=True)


def test_boot_ports_share_one_dict_for_lazy_resolution(project, monkeypatch):
    seen = {}

    def build_a(b):
        seen["ports"] = b.ports
        return "A"

    feats = [_feat("a", Port("a", build_a)), _feat("b", Port("b", lambda b: "B"))]
    monkeypatch.setattr(boot_mod, "discover", lambda: feats)
    monkeypatch.setattr(boot_mod, "check", lambda fs: [])
    b = boot_mod.boot(ports=True)
    try:
        assert seen["ports"] is b.ports
        assert seen["ports"]["b"] == "B"
    finally:
        b.conn.close()
