"""create_app: 루프백 Host 게이트·기능 라우터 마운트·health·정적 서빙·생성 파일 일치."""

from __future__ import annotations

from dataclasses import replace
from importlib import metadata
from pathlib import Path

from tests.kernel.web.conftest import make_client
from trader.kernel.registry import discover
from trader.kernel.web import gen

REPO = Path(__file__).resolve().parents[3]


def test_non_loopback_host_forbidden(client):
    r = client.get("/api/system/health", headers={"Host": "evil.example"})
    assert r.status_code == 403
    assert "detail" in r.json()


def test_loopback_hosts_allowed_with_and_without_port(client):
    for host in (
        "127.0.0.1",
        "127.0.0.1:8787",
        "localhost",
        "localhost:3000",
        "[::1]",
        "[::1]:8787",
    ):
        assert client.get("/api/system/health", headers={"Host": host}).status_code == 200, host


def test_extra_hosts_allowed(deps):
    c = make_client(replace(deps, extra_hosts=("pc.local",)))
    assert c.get("/api/system/health", headers={"Host": "pc.local:8787"}).status_code == 200
    assert c.get("/api/system/health", headers={"Host": "other.local"}).status_code == 403


def test_health_shape(deps_with_feature):
    body = make_client(deps_with_feature).get("/api/system/health").json()
    assert body == {"ok": True, "version": metadata.version("trader"), "features": ["fixture"]}


def test_feature_routes_mounted_under_api(deps_with_feature):
    c = make_client(deps_with_feature)
    assert c.get("/api/fixture/ping").json() == {"pong": True}


def test_unknown_api_path_is_json_404(client):
    r = client.get("/api/nope")
    assert r.status_code == 404 and "detail" in r.json()


def test_guide_page_when_dist_missing(client):
    r = client.get("/")
    assert r.status_code == 200
    assert "npm run build" in r.text


def test_static_dist_with_spa_fallback(deps):
    (deps.dist / "assets").mkdir(parents=True)
    (deps.dist / "index.html").write_text("<html>INDEX</html>", encoding="utf-8")
    (deps.dist / "assets" / "a.js").write_text("console.log(1)", encoding="utf-8")
    c = make_client(deps)
    assert "INDEX" in c.get("/").text
    assert "INDEX" in c.get("/orders/deep/link").text
    assert c.get("/assets/a.js").text == "console.log(1)"
    assert c.get("/api/nope").status_code == 404


def test_gen_sse_keys_format():
    from tests.kernel.web.conftest import fixture_feature

    out = gen.sse_keys_ts([fixture_feature()])
    assert 'export const SSE_KEYS = ["daemon", "fixture_count"] as const;' in out
    assert "export type SseKey = (typeof SSE_KEYS)[number];" in out
    assert 'export const NOISY_KEYS: SseKey[] = ["daemon", "fixture_count"];' in out
    assert out.endswith("\n")


def test_gen_sse_keys_matches_committed_file():
    committed = (REPO / "web/src/generated/sse-keys.ts").read_text(encoding="utf-8")
    assert committed == gen.sse_keys_ts(discover())
