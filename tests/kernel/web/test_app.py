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
    assert set(body) == {"level", "areas", "reasons", "checked_at", "version", "features"}
    assert body["version"] == metadata.version("trader") and body["features"] == ["fixture"]
    assert body["level"] == "fail"  # 데몬 없음 (원장 미초기화는 warn 사유로만)
    assert [r["level"] for r in body["reasons"] if r["area"] == "ledger"] == ["warn"]


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


def test_index_html_is_revalidated_but_assets_are_not(deps):
    """앱 업데이트 뒤 브라우저가 옛 index.html(옛 자산 해시)을 휴리스틱 캐시로 쓰지 않게."""
    (deps.dist / "assets").mkdir(parents=True)
    (deps.dist / "index.html").write_text("<html>INDEX</html>", encoding="utf-8")
    (deps.dist / "assets" / "index-AbC123xy.js").write_text("console.log(1)", encoding="utf-8")
    c = make_client(deps)
    for path in ("/", "/orders/deep/link", "/index.html"):
        r = c.get(path)
        assert "INDEX" in r.text
        assert "no-cache" in r.headers.get("cache-control", ""), path
        assert "no-store" not in r.headers.get("cache-control", "")
    asset = c.get("/assets/index-AbC123xy.js")
    assert "no-cache" not in asset.headers.get("cache-control", "")


def test_gen_sse_keys_format():
    from tests.kernel.web.conftest import fixture_feature

    out = gen.sse_keys_ts([fixture_feature()])
    kernel = '"daemon", "usage", "control", "jobs", "runs", "notices"'
    assert f"export const SSE_KEYS = [{kernel}, \"fixture_count\"] as const;" in out
    assert "export type SseKey = (typeof SSE_KEYS)[number];" in out
    # 커널 키 중 잦게 바뀌는 것은 daemon 하나뿐
    assert 'export const NOISY_KEYS: SseKey[] = ["daemon", "fixture_count"];' in out
    assert out.endswith("\n")


def test_gen_sse_keys_matches_committed_file():
    committed = (REPO / "web/src/generated/sse-keys.ts").read_text(encoding="utf-8")
    assert committed == gen.sse_keys_ts(discover())


def test_server_time_header_on_every_response(client):
    for path in ("/api/system/health", "/api/nothing", "/"):
        r = client.get(path)
        stamp = r.headers["x-server-time"]
        assert stamp.endswith("+00:00") or stamp.endswith("Z"), path
    gated = client.get("/api/system/health", headers={"Host": "evil.example"})
    assert gated.status_code == 403 and gated.headers["x-server-time"].endswith("+00:00")


def test_bare_api_and_all_methods_are_json_404(client):
    bare = client.get("/api")
    assert bare.status_code == 404 and bare.json()["detail"].startswith("없는 API")
    for method in ("GET", "POST", "PATCH", "DELETE", "PUT"):
        r = client.request(method, "/api/x")
        assert r.status_code == 404, method
        assert r.json()["detail"].startswith("없는 API"), method


def test_dist_checked_per_request(deps):
    c = make_client(deps)
    assert "npm run build" in c.get("/").text
    deps.dist.mkdir(parents=True)
    (deps.dist / "index.html").write_text("<html>INDEX</html>", encoding="utf-8")
    assert "INDEX" in c.get("/").text


def test_gen_nav_matches_committed_file():
    committed = (REPO / gen.REL_NAV_PATH).read_text(encoding="utf-8")
    assert committed == gen.nav_ts(discover())
