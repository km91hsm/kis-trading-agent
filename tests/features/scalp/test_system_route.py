"""scalp 「시스템」: `GET /system/scalp?market=`(옛 `system.scalp_status`) — 설정·시장별 오늘
진입/청산·중단 사유·판정 대기·판정자 요약. 상태 파일만 읽는다."""

from __future__ import annotations

from tests.features.scalp.conftest import D
from tests.features.scalp.test_routes import _pos, client  # noqa: F401 — 픽스처 재사용
from trader.features.scalp.state import save_state


def test_system_scalp_status(project, client):  # noqa: F811
    cfg = client.app.state.deps.settings.scalp
    r = client.get("/api/system/scalp").json()
    assert r["enabled"] is cfg.enabled and r["max_positions"] == cfg.max_positions
    assert r["judge"] == cfg.judge and r["max_weight_pct"] == cfg.max_weight_pct
    kr = r["markets"]["KR"]
    assert kr["date"] == D.isoformat() and kr["open"] == [] and kr["halted"] is None
    assert kr["judge"]["counts"] == {"go": 0, "no_go": 0, "none": 0}
    save_state(project / "state", D, "KR", {
        "open": [_pos("005930", {"paper-main": 3})], "closed": [], "halted": "일일 손실",
        "pending": {"000660": {}, "035420": {}},
    })  # fmt: skip
    kr = client.get("/api/system/scalp", params={"market": "KR"}).json()["markets"]
    assert list(kr) == ["KR"]
    assert kr["KR"]["open"][0]["ticker"] == "005930" and kr["KR"]["halted"] == "일일 손실"
    assert kr["KR"]["pending"] == ["000660", "035420"]
    assert client.get("/api/system/scalp", params={"market": "XX"}).status_code == 400
