"""데몬 잡 news_collect: 등록·시장 단위 격리·보존·훅 실패 격리·세이브티커 폴더 적재."""

import shutil
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from pydantic import ValidationError

from tests.features.ingest.helpers import load_bytes
from trader.features.ingest import job, news_store
from trader.features.ingest.feature import FEATURE
from trader.features.ingest.settings import SourcesConfig
from trader.kernel.cli.boot import build_registry
from trader.kernel.config.secrets import Secrets
from trader.kernel.daemon import jobs as jobs_mod
from trader.kernel.daemon.runner import DaemonDeps
from trader.kernel.ledger import db
from trader.kernel.registry import discover
from trader.kernel.registry.types import Hook

NOW = datetime(2026, 10, 12, 1, 0, tzinfo=UTC)  # 픽스처 기사(10-09)는 보존 기간 안
GOOGLE = "https://news.google.com/rss/search"
SAMPLE = Path(__file__).parent.parent / "saveticker" / "fixtures" / "saveticker"


def _deps(project, *, hooks=None, markets=("KR", "US"), now=NOW, notifier=None):
    path = project / "state" / "t.db"

    def factory():
        return db.connect(path)

    from trader.kernel.registry.contracts import all_migrations

    c = factory()
    db.init_schema(c, all_migrations(discover()))
    c.close()
    return DaemonDeps(
        settings=SimpleNamespace(), schedule=SimpleNamespace(), conn_factory=factory,
        state=project / "state", now=lambda: now, markets=list(markets), gates={},
        stop_event=threading.Event(), dry_run=False, secrets=Secrets(),
        registry=build_registry(discover()), hooks=hooks, notifier=notifier,
    )  # fmt: skip


def _old_and_new(conn, market):
    rows = [
        {"title": "옛 기사", "url": f"https://a/old/{market}",
         "published_at": (NOW - timedelta(days=20)).isoformat(),
         "received_at": (NOW - timedelta(days=20)).isoformat()},
    ]  # fmt: skip
    news_store.insert_items(conn, rows, market=market, source="t", query="q", lang="ko", names={})


def test_build_jobs_includes_news_collect_interval(project):
    deps = _deps(project)
    schedule = SimpleNamespace(news_collect=SimpleNamespace(interval_s=1800))
    built = {
        j.id: j
        for j in jobs_mod.build(
            [FEATURE], schedule=schedule, settings=deps.settings, markets=["KR"], ctx=deps
        )
    }
    assert built["news_collect"].spec.kind == "news_collect"
    assert built["news_collect"].trigger_kwargs == {"seconds": 1800}
    assert not built["news_collect"].spec.heavy


def test_news_body_collects_per_market_and_prunes(project, respx_mock):
    respx_mock.get(url__startswith=GOOGLE).mock(
        return_value=httpx.Response(200, content=load_bytes("news_google.xml"))
    )
    deps = _deps(project)
    c = deps.conn_factory()
    _old_and_new(c, "KR")
    c.close()
    out = job.news_collect(deps, job.JOB)
    assert out["KR"]["inserted"] >= 1 and out["US"]["inserted"] >= 1
    assert out["pruned"] == 1
    assert out["hooks"] == []
    c = deps.conn_factory()
    try:
        assert news_store.count(c, "KR") >= 1
    finally:
        c.close()


def test_news_body_skipped_when_disabled(project, monkeypatch):
    monkeypatch.setattr(job, "load_sources", lambda **kw: SourcesConfig(news={"enabled": False}))
    assert job.news_collect(_deps(project), job.JOB) == {"skipped": "news.enabled=false"}


def test_news_body_ingests_saveticker_folder(project, monkeypatch):
    shutil.copytree(SAMPLE, project / "state" / "external" / "saveticker")
    monkeypatch.setattr(news_store, "collect", lambda *a, **k: {"inserted": 0})
    deps = _deps(project, now=datetime(2026, 10, 25, 3, 0, tzinfo=UTC))  # 샘플 관측 뒤
    out = job.news_collect(deps, job.JOB)
    assert out["saveticker"]["inserted"] > 0
    c = deps.conn_factory()
    try:
        assert news_store.count(c) > 0
    finally:
        c.close()
    assert job.news_collect(deps, job.JOB)["saveticker"]["inserted"] == 0


def test_news_collect_restores_retained_korean_tags_from_the_original_us_row(project, monkeypatch):
    monkeypatch.setattr(news_store, "collect", lambda *a, **k: {"inserted": 0})
    deps = _deps(project)
    received = NOW - timedelta(days=8)
    published = received - timedelta(hours=3)
    url = "https://example.org/synthetic/10"
    original = {
        "title": "합성 과거 기사",
        "summary": "당시 저장한 본문",
        "published_at": published.isoformat(),
        "received_at": received.isoformat(),
        "tickers": ["0256C0.KR", "005930.KR", "USX", "USY"],
        "url": url,
    }
    c = deps.conn_factory()
    try:
        for source, query, article_url in (
            ("saveticker:local", "news_all", url),
            ("google_rss", "news_all", "https://example.org/other"),
            ("saveticker:local", "unknown", "https://example.org/unknown"),
        ):
            news_store.insert_items(
                c,
                [{**original, "url": article_url}],
                market="US",
                source=source,
                query=query,
                lang="ko",
                names={},
                extra_tickers=True,
            )
        us_id = c.execute("SELECT id FROM news_items WHERE url=?", (url,)).fetchone()[0]
        for expected_inserts in (1, 0):
            result = job.news_collect(deps, job.JOB)
            assert result["saveticker_kr_restored"] == expected_inserts
            rows = news_store.recent(c, "KR", asof=received, ticker="0256C0")
            assert [(r["url"], r["tickers"]) for r in rows] == [(url, ["005930", "0256C0"])]
            assert (rows[0]["source"], rows[0]["query"]) == ("saveticker:local", "news_all")
            for field in ("title", "summary", "published_at", "received_at"):
                assert rows[0][field] == original[field]
            assert not news_store.recent(c, "KR", asof=received - timedelta(seconds=1))
            us = news_store.recent(c, "US", asof=received, ticker="USX")
            assert [(r["id"], r["tickers"]) for r in us if r["url"] == url] == [
                (us_id, ["USX", "USY"])
            ]
            assert news_store.count(c, "KR") == 1
    finally:
        c.close()


def test_news_body_isolates_market_failure(project, respx_mock, monkeypatch):
    respx_mock.get(url__startswith=GOOGLE).mock(
        return_value=httpx.Response(200, content=load_bytes("news_google.xml"))
    )
    real = news_store.collect

    def flaky(conn, cfg, secrets, *, market, **kw):
        if market == "KR":
            raise RuntimeError("rss down")
        return real(conn, cfg, secrets, market=market, **kw)

    monkeypatch.setattr(news_store, "collect", flaky)
    deps = _deps(project)
    c = deps.conn_factory()
    _old_and_new(c, "US")
    c.close()
    out = job.news_collect(deps, job.JOB)
    assert out["KR"] == {"error": "RuntimeError: rss down"}
    assert out["US"]["inserted"] >= 1 and out["pruned"] == 1


def test_news_body_stops_before_markets_when_stop_event_set(project, monkeypatch):
    calls = []
    monkeypatch.setattr(news_store, "collect", lambda *a, **k: calls.append(k) or {})
    deps = _deps(project)
    deps.stop_event.set()
    out = job.news_collect(deps, job.JOB)
    assert isinstance(out, dict) and calls == [] and "KR" not in out and "US" not in out


def test_post_ingest_hook_receives_notifier(project, monkeypatch):
    monkeypatch.setattr(news_store, "collect", lambda *a, **k: {"inserted": 0})
    seen = {}
    hooks = {"news.post_ingest": (Hook("news.post_ingest", lambda **kw: seen.update(kw)),)}
    sentinel = object()
    deps = _deps(project, hooks=hooks, notifier=sentinel)
    job.news_collect(deps, job.JOB)
    assert seen["notifier"] is sentinel
    assert {"conn", "markets", "now", "result"} <= set(seen)


def test_post_ingest_hook_receives_deps(project, monkeypatch):
    monkeypatch.setattr(news_store, "collect", lambda *a, **k: {"inserted": 0})
    seen = {}
    hooks = {"news.post_ingest": (Hook("news.post_ingest", lambda **kw: seen.update(kw)),)}
    deps = _deps(project, hooks=hooks)
    job.news_collect(deps, job.JOB)
    assert seen["deps"] is deps


def test_post_ingest_hook_failure_does_not_fail_job(project, monkeypatch):
    monkeypatch.setattr(news_store, "collect", lambda *a, **k: {"inserted": 0})

    def boom(**kw):
        raise ValueError("hook down")

    hooks = {"news.post_ingest": (Hook("news.post_ingest", boom),)}
    out = job.news_collect(_deps(project, hooks=hooks), job.JOB)
    assert "ValueError" in out["hooks"][0]["error"]
    assert "error" not in out


def test_news_collect_queue_kind_runs_same_body(project, monkeypatch):
    calls = []

    def fake(ctx, markets):
        calls.append(list(markets))
        return {"markets": list(markets)}

    monkeypatch.setattr(job, "_collect", fake)
    deps = _deps(project)
    (kind,) = [q for q in FEATURE.queue_kinds if q.kind == "news_collect"]
    assert (kind.params_model, kind.label, kind.lane, kind.priority) == (
        job.NewsCollectParams, "뉴스 수집", "info", 0,
    )  # fmt: skip
    assert not kind.trading and not kind.restart_requeue and kind.body is job.news_collect_request
    params = job.NewsCollectParams.model_validate({}).model_dump()
    assert kind.body(deps, params, lambda _t: None) == {"markets": ["KR", "US"]}
    assert kind.body(deps, {"market": "US"}, lambda _t: None) == {"markets": ["US"]}
    assert job.news_collect(deps, job.JOB) == {"markets": ["KR", "US"]}
    assert calls == [["KR", "US"], ["US"], ["KR", "US"]]
    with pytest.raises(ValidationError):
        job.NewsCollectParams.model_validate({"market": "KR", "x": 1})


def test_news_collect_params_market_is_literal():
    """market 은 `Market`(KR|US) — 소문자 등은 검증 오류(텔레그램 `/run` 오류 문구, FR-5)."""
    assert job.NewsCollectParams.model_validate({"market": "US"}).market == "US"
    for bad in ("kr", "JP", ""):
        with pytest.raises(ValidationError):
            job.NewsCollectParams.model_validate({"market": bad})


def test_news_collect_request_intersects_configured_markets(project, monkeypatch):
    """요청 시장 중 설정(`deps.markets`)에 없는 것은 수집하지 않고 `skipped` 로 적는다(FR-5)."""
    calls = []

    def fake(ctx, markets):
        calls.append(list(markets))
        return {"markets": list(markets)}

    monkeypatch.setattr(job, "_collect", fake)
    deps = _deps(project, markets=("KR",))
    out = job.news_collect_request(deps, {"market": "US"}, lambda _t: None)
    assert out == {"skipped": ["US"]}
    assert calls == []
    assert job.news_collect_request(deps, {"market": "KR"}, lambda _t: None) == {"markets": ["KR"]}
    assert job.news_collect_request(deps, {}, lambda _t: None) == {"markets": ["KR"]}
    assert calls == [["KR"], ["KR"]]


def test_saveticker_sync_reads_completed_local_articles_before_the_next_full_collection(project):
    from trader.features.saveticker.models import Capture, Item
    from trader.features.saveticker.store import Store

    deps = _deps(project)
    store = Store(project / "state/external/saveticker/data")

    def save(at, items):
        store.save(
            Capture(
                stream="news_all",
                page_url="https://www.saveticker.com/news",
                observed_at=at,
                items=items,
            )
        )

    pending = Item(id="one", title="합성 기사", detail_status="pending")
    complete = pending.model_copy(update={"body": "확인된 본문", "detail_status": "ok"})
    save(NOW - timedelta(seconds=30), [pending])
    save(NOW, [complete, Item(id="two", title="합성 속보", detail_status="ok")])
    save(NOW + timedelta(seconds=1), [Item(id="future", title="미래 기사", detail_status="ok")])
    built = {
        j.id: j
        for j in jobs_mod.build(
            [FEATURE],
            schedule=deps.schedule,
            settings=deps.settings,
            markets=deps.markets,
            ctx=deps,
        )
    }
    assert "saveticker_news_sync" in built
    sync = built["saveticker_news_sync"]
    assert sync.interval_s == 60
    assert sync.spec.body(deps, sync)["inserted"] == 4
    assert sync.spec.body(deps, sync)["inserted"] == 0
    c = deps.conn_factory()
    try:
        rows = news_store.recent(c, "KR", asof=NOW, source="saveticker")
        assert {r["title"]: r["summary"] for r in rows} == {
            "합성 기사": "확인된 본문",
            "합성 속보": "",
        }
        assert all(r["received_at"] == NOW.isoformat() for r in rows)
    finally:
        c.close()


def test_news_collect_replays_retained_archive_after_week_offline(project, monkeypatch):
    import json

    day = NOW - timedelta(days=6)
    deps = _deps(project, now=day - timedelta(days=1))
    monkeypatch.setattr(news_store, "collect", lambda *a, **kw: {})
    root = project / "state/external/saveticker/data"
    root.mkdir(parents=True)
    job.news_collect(deps, job.JOB)
    folder = root / day.date().isoformat()
    folder.mkdir()
    row = {
        "stream": "news_all",
        "revision": 1,
        "received_at": day.isoformat(),
        "item": {"id": "archived", "title": "중단 동안 수집한 기사", "tickers": ["QQQ"]},
    }
    (folder / "news.jsonl").write_text(json.dumps(row), encoding="utf-8")
    deps = _deps(project, now=NOW)
    result = job.news_collect(deps, job.JOB)
    assert result["saveticker"]["inserted"] == 1
    assert job.news_collect(deps, job.JOB)["saveticker"]["inserted"] == 0
