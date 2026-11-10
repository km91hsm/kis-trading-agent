"""단타 판정자 켜기·끄기 화면 설정: control `scalp_judge`(bool)가 yaml `scalp.judge` 보다 우선하고,
데몬 틱(판정자 풀·진입 판정)·`/settings/scalp`·`/system/scalp`·`scalp report` 가 같은 실효값을 쓴다.
`PATCH /settings/scalp-judge`(토큰, null 이면 yaml 로)는 control 이력을 남긴다."""

from __future__ import annotations

from tests.features.conftest import AUTH
from tests.features.scalp.test_routes import client  # noqa: F401
from trader.features.scalp import switch
from trader.kernel.ledger.control import get_flag, set_flag

URL = "/api/settings/scalp-judge"


def test_judge_enabled_prefers_control(conn, settings):
    off = settings.model_copy(update={"scalp": settings.scalp.model_copy(update={"judge": False})})
    assert switch.judge_enabled(conn, off) == (False, "yaml")
    set_flag(conn, switch.JUDGE_FLAG, True)
    assert switch.judge_enabled(conn, off) == (True, "control")
    eff = switch.with_judge(conn, off)
    assert eff.scalp.judge is True and off.scalp.judge is False  # 원본은 그대로
    set_flag(conn, switch.JUDGE_FLAG, "yes")  # bool 이 아니면 무시
    assert switch.judge_enabled(conn, off) == (False, "yaml")


def test_patch_judge_requires_token_persists_and_audits(client):  # noqa: F811
    assert client.patch(URL, json={"enabled": True}).status_code == 401
    assert client.patch(URL, json={"enabled": "x"}, headers=AUTH).status_code == 422
    r = client.patch(URL, json={"enabled": True}, headers=AUTH)
    assert r.status_code == 200
    body = r.json()
    assert body["judge"] is True and body["judge_source"] == "control"
    assert client.get("/api/settings/scalp").json()["judge"] is True
    assert client.get("/api/system/scalp").json()["judge"] is True
    with client.app.state.deps.conn_factory() as conn:
        assert get_flag(conn, switch.JUDGE_FLAG) is True
        row = conn.execute("SELECT channel, key, new FROM control_log ORDER BY id DESC").fetchone()
    assert (row["channel"], row["key"], row["new"]) == ("web", switch.JUDGE_FLAG, "true")
    r = client.patch(URL, json={"enabled": None}, headers=AUTH)
    assert r.json()["judge_source"] == "yaml"
