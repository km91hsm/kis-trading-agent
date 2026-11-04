"""kernel/ops health 러너: 영역 순회·실패 격리·키 접두·격상·헤드라인·웹 변환."""

import logging

from trader.kernel.ledger.control import get_flag
from trader.kernel.ops import health
from trader.kernel.ops.health import Item
from trader.kernel.registry.types import Feature, HealthArea

from .conftest import NOW


def test_check_ok_when_ledger_empty_except_daemon_dead(make_ctx):
    r = health.check(make_ctx())
    keys = {i["key"] for i in r["items"]}
    assert r["level"] == "critical" and r["label"] == "경보" and r["at"] == NOW.isoformat()
    assert "jobs.daemon" in keys and r["areas"]["jobs"] == "critical"
    assert r["areas"]["llm"] == "ok" and r["areas"]["orders"] == "ok"
    assert list(r["areas"]) == ["jobs", "llm", "orders"]
    assert r["actions"] and all(a["link"] for a in r["actions"])


def test_check_isolates_failing_area(make_ctx):
    boom = Feature(id="boom", tier="service", health_areas=(HealthArea("boom", lambda ctx: 1 / 0),))
    r = health.check(make_ctx(features=[boom]))
    by = {i["key"]: i for i in r["items"]}
    assert by["boom.error"]["level"] == "warning"
    assert by["boom.error"]["text"].startswith("점검 실패: ZeroDivisionError")
    assert r["areas"]["boom"] == "warning"
    assert r["areas"]["jobs"] == "critical" and "jobs.daemon" in by  # 다른 영역은 그대로
    assert list(r["areas"]) == ["jobs", "llm", "orders", "boom"]


def test_check_isolates_bad_return_values(make_ctx):
    """영역이 Item 이 아닌 값을 주거나 단계가 LEVELS 밖이면 그 영역만 `{key}.error` warning."""
    junk = Feature(id="junk", tier="service", health_areas=(HealthArea("junk", lambda ctx: [1]),))
    odd = Feature(
        id="odd",
        tier="service",
        health_areas=(HealthArea("odd", lambda ctx: [Item("odd.x", "boom", "이상한 단계")]),),
    )
    scalar = Feature(id="sc", tier="service", health_areas=(HealthArea("sc", lambda ctx: 3),))
    r = health.check(make_ctx(features=[junk, odd, scalar]))
    by = {i["key"]: i for i in r["items"]}
    for key in ("junk", "odd", "sc"):
        assert by[f"{key}.error"]["level"] == "warning" and r["areas"][key] == "warning"
    assert "odd.x" not in by
    assert r["areas"]["jobs"] == "critical" and r["level"] == "critical"


def test_kernel_areas_match_reserved_keys():
    from trader.kernel.ops.areas import KERNEL_AREAS
    from trader.kernel.ops.types import RESERVED_AREA_KEYS

    assert health.KERNEL_AREAS is KERNEL_AREAS
    assert {a.key for a in KERNEL_AREAS} == set(RESERVED_AREA_KEYS) == {"jobs", "llm", "orders"}


def test_check_prefixes_foreign_keys(make_ctx, caplog):
    x = Feature(
        id="x",
        tier="service",
        health_areas=(
            HealthArea("x", lambda ctx: [Item("y.z", "watch", "남의 키"), Item("x.ok", "ok", "-")]),
        ),
    )
    with caplog.at_level(logging.WARNING, logger="trader.ops"):
        r = health.check(make_ctx(features=[x]))
    keys = {i["key"] for i in r["items"]}
    assert "x.y.z" in keys and "y.z" not in keys
    assert "x.ok" not in keys  # ok 항목은 items 에 넣지 않는다
    assert r["areas"]["x"] == "watch"
    assert any("y.z" in rec.getMessage() for rec in caplog.records)


def test_escalate_only_when_worse_and_headline(conn):
    r_ok = {"level": "ok", "label": "정상", "at": NOW.isoformat(), "items": []}
    assert health.escalate(conn, r_ok) is None
    r_warn = {
        "level": "warning",
        "label": "주의",
        "at": NOW.isoformat(),
        "items": [{"key": "orders.unknown", "level": "warning", "text": "unknown 1건"}],
    }
    msg = health.escalate(conn, r_warn)
    assert msg and msg.startswith("[점검 격상] 정상 → 주의") and "unknown 1건" in msg
    assert health.escalate(conn, r_warn) is None  # 같은 단계면 다시 알리지 않는다
    assert health.escalate(conn, r_ok) is None  # 좋아지면 기록만
    assert get_flag(conn, health.LEVEL_FLAG)["level"] == "ok"
    assert health.headline(r_ok) == "점검 정상 · 17:30"
    assert health.headline(r_warn) == "점검 주의 · 1건 · 17:30"
    assert health.headline(None) == "점검 기록 없음"


def test_escalate_alerts_new_warning_key_at_same_level(conn):
    base = {"level": "warning", "label": "주의", "at": NOW.isoformat()}
    old = {"key": "jobs.errors", "level": "warning", "text": "잡 오류 3건"}
    new = {"key": "llm.cap", "level": "warning", "text": "세션 상한 도달"}
    assert health.escalate(conn, {**base, "items": [old]})
    assert health.escalate(conn, {**base, "items": [old]}) is None
    msg = health.escalate(conn, {**base, "items": [old, new]})
    assert msg and "새 경고 1건" in msg and "세션 상한" in msg and "잡 오류" not in msg


def test_store_and_latest_roundtrip(conn):
    assert health.latest(conn) is None
    r = {"level": "ok", "label": "정상", "at": NOW.isoformat(), "areas": {}, "items": []}
    health.store(conn, r)
    assert health.latest(conn) == r


def _result(*items):
    level = health.worst([i["level"] for i in items])
    label = health.LABEL[level]
    return {"level": level, "label": label, "at": NOW.isoformat(), "items": list(items)}


def test_to_web_maps_levels_and_drops_ok():
    crit = {"key": "jobs.daemon", "level": "critical", "text": "데몬 꺼짐", "link": "/system"}
    warn = {"key": "orders.unknown", "level": "warning", "text": "unknown", "link": "/orders"}
    watch = {"key": "llm.cap", "level": "watch", "text": "상한 근접", "link": None}
    ok = {"key": "x.fine", "level": "ok", "text": "-", "link": None}
    w = health.to_web(_result(crit, warn, watch, ok), version="9.9", features=["spot"])
    assert w["level"] == "fail" and w["checked_at"] == NOW.isoformat()
    assert w["version"] == "9.9" and w["features"] == ["spot"]
    assert [(r["area"], r["level"], r["key"]) for r in w["reasons"]] == [
        ("jobs", "fail", "jobs.daemon"),
        ("orders", "warn", "orders.unknown"),
        ("llm", "info", "llm.cap"),
    ]
    assert w["reasons"][1]["link"] == "/orders" and w["reasons"][0]["text"] == "데몬 꺼짐"
    assert health.to_web(_result(warn), version="v", features=[])["level"] == "warn"
    only_watch = health.to_web(_result(watch), version="v", features=[])
    assert only_watch["level"] == "ok" and only_watch["reasons"][0]["level"] == "info"
    assert health.to_web(_result(), version="v", features=[])["level"] == "ok"
