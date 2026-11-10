"""quotes health 영역: 체결통보 리스너가 웹소켓 세션을 쥔 동안(`ws_session_busy:<env>`) watch."""

from datetime import UTC, datetime, timedelta

import pytest

from trader.features.quotes import health
from trader.kernel.ledger import db
from trader.kernel.ledger.control import set_flag
from trader.kernel.ops.types import OpsContext
from trader.kernel.registry import discover
from trader.kernel.registry.contracts import all_migrations

NOW = datetime(2026, 10, 27, 1, 0, tzinfo=UTC)


@pytest.fixture
def conn(project):
    c = db.connect()
    db.init_schema(c, all_migrations(discover()))
    try:
        yield c
    finally:
        c.close()


def ctx(conn, project):
    return OpsContext(conn, None, project / "state", NOW, {}, [], [])


def test_area_key_is_feature_id_and_registered():
    from trader.features.quotes.feature import FEATURE

    assert health.AREA.key == "quotes" and FEATURE.health_areas == (health.AREA,)


def test_session_busy_watch_only_when_until_future(conn, project):
    assert health.check(ctx(conn, project)) == []
    future = (NOW + timedelta(seconds=60)).isoformat()
    set_flag(conn, "ws_session_busy:real", {"until": future})
    set_flag(conn, "ws_session_busy:paper", {"until": (NOW - timedelta(seconds=1)).isoformat()})
    set_flag(conn, "ws_session_busy:demo", {"until": "깨진 값"})
    set_flag(conn, "ws_session_busy:naive", {"until": "2026-10-27T01:00:30"})  # tz 없음 → UTC
    items = health.check(ctx(conn, project))
    assert [(i.key, i.level, i.link) for i in items] == [
        ("quotes.session_busy.naive", "watch", "/data#quotes"),
        ("quotes.session_busy.real", "watch", "/data#quotes"),
    ]
    assert "real" in items[1].text and future in items[1].text
