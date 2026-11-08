from tests.kernel.web.conftest import AUTH, ledger_deps, make_client


def test_codex_toggle_is_authenticated_persistent_and_audited(project, monkeypatch):
    from trader.kernel.platform import codex_bin

    monkeypatch.setattr(
        codex_bin,
        "find_codex",
        lambda *_: (_ for _ in ()).throw(codex_bin.CodexNotFound("missing")),
    )
    deps = ledger_deps(project)
    deps.settings.debate_rounds = 0
    client = make_client(deps)
    old = client.get("/api/settings/codex-debate")
    assert old.status_code == 200
    assert old.json()["enabled"] is False and old.json()["available"] is False
    assert client.patch("/api/settings/codex-debate", json={"enabled": True}).status_code == 401
    out = client.patch("/api/settings/codex-debate", headers=AUTH, json={"enabled": True})
    assert out.status_code == 200 and out.json()["source"] == "control"
    assert out.json()["applies_to"] == "next_new_research" and out.json()["effective_rounds"] == 1
    assert make_client(deps).get("/api/settings/codex-debate").json()["enabled"] is True
    assert client.get("/api/settings/codex-debate").json()["usage"]["cost_usd"] is None
    with deps.conn_factory() as conn:
        row = conn.execute("SELECT key,new FROM control_log ORDER BY id DESC LIMIT 1").fetchone()
    assert row["key"] == "codex_debate_enabled" and row["new"] == "true"
    assert (
        client.patch("/api/settings/codex-debate", headers=AUTH, json={"enabled": None}).json()[
            "source"
        ]
        == "yaml"
    )


def test_codex_throttle_and_call_cap_apis(project, monkeypatch):
    """설정 화면: Codex 사용량 조절 켜기·끄기와 하루 호출 제한(토큰 필요, null 이면 yaml),
    control 이력, /settings/codex-debate 상태에 실효 값과 출처."""
    from trader.kernel.platform import codex_bin
    from trader.kernel.session import research_policy as p

    monkeypatch.setattr(
        codex_bin, "find_codex", lambda *_: (_ for _ in ()).throw(codex_bin.CodexNotFound("x"))
    )
    deps = ledger_deps(project)
    c = make_client(deps)
    t_url, cap_url = "/api/settings/codex-throttle", "/api/settings/codex-call-cap"
    assert c.patch(t_url, json={"enabled": False}).status_code == 401
    assert c.patch(cap_url, json={"cap": 5}).status_code == 401
    assert c.patch(t_url, json={"enabled": "x"}, headers=AUTH).status_code == 422
    for bad in (0, 10001, "x"):
        assert c.patch(cap_url, json={"cap": bad}, headers=AUTH).status_code == 422, bad
    r = c.patch(t_url, json={"enabled": False}, headers=AUTH)
    assert r.status_code == 200 and r.json()["throttle"]["enabled"] is False
    assert r.json()["throttle"]["source"] == "control"
    r = c.patch(cap_url, json={"cap": 5}, headers=AUTH)
    assert r.status_code == 200
    assert r.json()["usage"]["daily_call_cap"] == 5 and r.json()["usage"]["cap_source"] == "control"
    st = c.get("/api/settings/codex-debate").json()
    assert st["throttle"]["enabled"] is False and st["usage"]["daily_call_cap"] == 5
    with deps.conn_factory() as conn:
        keys = [r["key"] for r in conn.execute("SELECT key FROM control_log ORDER BY id")]
    assert keys == [p.CODEX_THROTTLE_FLAG, p.CODEX_CAP_FLAG]
    r = c.patch(t_url, json={"enabled": None}, headers=AUTH).json()
    assert r["throttle"] == {**r["throttle"], "enabled": True, "source": "yaml"}
    r = c.patch(cap_url, json={"cap": None}, headers=AUTH).json()
    assert r["usage"]["daily_call_cap"] == 80 and r["usage"]["cap_source"] == "yaml"
