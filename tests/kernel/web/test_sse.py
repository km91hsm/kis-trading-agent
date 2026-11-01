"""SSE 지문 조립·스트림: 바뀔 때만 change, once=1 은 1회, probe 예외는 스트림을 죽이지 않는다."""

from __future__ import annotations

import asyncio
import json
from dataclasses import replace

from tests.kernel.web.conftest import make_client
from trader.kernel.registry import Feature, SseKey
from trader.kernel.web import sse


def _data(event: str) -> dict:
    line = next(x for x in event.splitlines() if x.startswith("data: "))
    return json.loads(line[6:])


def test_snapshot_collects_feature_keys_and_daemon(deps_with_feature):
    snap = sse.snapshot(deps_with_feature)
    assert snap["fixture_count"] == 1
    assert snap["daemon"] == {"alive": False, "minute": ""}


def test_daemon_key_reduced_to_alive_and_minute(deps):
    hb = deps.state / "daemon_heartbeat.json"
    hb.write_text(
        json.dumps({"pid": 1, "updated_at": "2026-11-02T01:02:03+00:00"}), encoding="utf-8"
    )
    snap = sse.snapshot(deps)
    assert snap["daemon"]["minute"] == "2026-11-02T01:02"
    assert set(snap["daemon"]) == {"alive", "minute"}


def test_daemon_fp_bad_heartbeat_falls_back(deps):
    (deps.state / "daemon_heartbeat.json").write_text(
        json.dumps({"pid": 1, "updated_at": 123}), encoding="utf-8"
    )
    assert sse.snapshot(deps)["daemon"] == {"alive": False, "minute": ""}


def test_probe_exception_is_isolated(deps):
    def boom(_deps):
        raise RuntimeError("x")

    feat = Feature(
        id="f", tier="service", sse_keys=(SseKey("bad", boom), SseKey("good", lambda d: 7))
    )
    snap = sse.snapshot(replace(deps, features=[feat]))
    assert snap["bad"] == "" and snap["good"] == 7


def test_stream_emits_change_only_when_snapshot_changes(deps):
    values = iter([1, 1, 1, 2, 2])
    feat = Feature(id="f", tier="service", sse_keys=(SseKey("k", lambda d: next(values)),))
    d = replace(deps, features=[feat])

    async def run():
        agen = sse.stream(d, poll_s=0.0, share_s=0.0, ping_s=0.0)
        try:
            return [await agen.__anext__() for _ in range(5)]
        finally:
            await agen.aclose()

    ev = asyncio.run(run())
    kinds = [e.split("\n")[0] for e in ev]
    assert kinds == ["event: change", ": ping", ": ping", "event: change", ": ping"]
    assert _data(ev[0])["k"] == 1 and _data(ev[3])["k"] == 2


def test_stream_once_yields_exactly_one_change(deps_with_feature):
    async def run():
        return [e async for e in sse.stream(deps_with_feature, poll_s=0.0, once=True)]

    ev = asyncio.run(run())
    assert len(ev) == 1 and ev[0].startswith("event: change")


def test_stream_survives_raising_probe(deps):
    calls = {"n": 0}

    def flaky(_d):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("일시 오류")
        return calls["n"]

    d = replace(deps, features=[Feature(id="f", tier="service", sse_keys=(SseKey("k", flaky),))])

    async def run():
        agen = sse.stream(d, poll_s=0.0, share_s=0.0, ping_s=0.0)
        try:
            return [await agen.__anext__() for _ in range(2)]
        finally:
            await agen.aclose()

    ev = asyncio.run(run())
    assert ev[0].startswith("event: change") and ev[1].startswith("event: change")


def test_events_endpoint_once(deps_with_feature):
    r = make_client(deps_with_feature).get("/api/events?once=1")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/event-stream")
    assert r.text.count("event: change") == 1
    assert _data(r.text)["fixture_count"] == 1
