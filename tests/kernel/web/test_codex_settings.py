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
