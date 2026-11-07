"""scan 트리거(`news.post_ingest` 훅): 뉴스·공시 재분석, 장중 발굴 큐 kind `discover`.

옛 `test_daemon_news` 5·6·7·8·9·11, `test_daemon_disclosure` 1·2·3, `test_daemon` 의
`test_news_reanalyze_not_queued_while_throttled` 이식. 치환: `daemon._news_trigger(conn=, deps=,
market=, universe=)`→`triggers.news_trigger(deps, conn, market, universe)`,
`jobq.list_recent`→`job_requests` 직접 조회, `control.set_flag(paused)`→accounts `modes`,
`deps.brokers`→`ports["accounts"].brokers()`(리스트), `deps.input_deps.dart`→`dart=` 인자,
`deps.universe`→SimpleNamespace 유니버스. 단언은 그대로."""

import json
import threading
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace

from tests.features.scan.conftest import Sent
from trader.features.ingest import api as ingest_api
from trader.features.scan import triggers
from trader.features.scan.feature import FEATURE as SCAN
from trader.features.spot.feature import FEATURE as SPOT
from trader.kernel.broker.fake import FakeBroker
from trader.kernel.broker.models import BrokerAccount, Position
from trader.kernel.daemon.queue import kinds_of
from trader.kernel.daemon.queue_jobs import QueueState
from trader.kernel.daemon.runner import DaemonDeps
from trader.kernel.ledger import control, db

NOW = datetime(2026, 10, 24, 1, 0, tzinfo=UTC)  # 10:00 KST
UNI = SimpleNamespace(kr=["005930", "000660"], us=[], watch=[], exclude=[])


class Port:
    def __init__(self, brokers=()):
        self._b = list(brokers)

    def brokers(self):
        return list(self._b)

    def accounts(self):
        return [b.account for b in self._b]

    def market_client(self):
        return None

    def paused(self, conn):
        return bool(control.get_flag(conn, "paused"))


def trigger_deps(
    project, conn, *, now=None, dry_run=False, notifier=None, brokers=(), settings=None,
    in_window=True,
) -> DaemonDeps:  # fmt: skip
    from trader.kernel.cli.boot import build_registry
    from trader.kernel.config.loader import load
    from trader.kernel.registry import discover

    settings = settings or load(
        "settings", build_registry(discover()), path=project / "config" / "settings.yaml"
    )
    when = now or NOW
    return DaemonDeps(
        settings=settings,
        schedule=None,
        conn_factory=db.connect,
        state=project / "state",
        now=lambda: when,
        markets=["KR"],
        gates={"monitor": lambda job: None if in_window else "창 밖"},
        stop_event=threading.Event(),
        dry_run=dry_run,
        notifier=notifier,
        ports={"accounts": Port(brokers)},
        queue=QueueState(kinds_of([SCAN, SPOT])),
    )


def _news(conn, items, *, market="KR", names=None, received=None):
    rows = []
    for i, (title, tickers) in enumerate(items):
        rows.append(
            {
                "title": title,
                "url": f"https://x/{abs(hash(title)) % 10**8}/{i}",
                "published_at": (received or NOW).isoformat(),
                "received_at": (received or NOW).isoformat(),
                "tickers": tickers,
            }
        )
    ingest_api.insert_items(
        conn, rows, market=market, source="t", query="q", lang="ko", names=names or {},
        extra_tickers=True,
    )  # fmt: skip


def _queued(conn):
    return [
        (r["kind"], json.loads(r["params"]), r["source"])
        for r in conn.execute("SELECT * FROM job_requests ORDER BY id").fetchall()
        if r["status"] == "queued"
    ]


def _news_trigger(deps, conn, market="KR"):
    return triggers.news_trigger(deps, conn, market, UNI)


# --- 뉴스 트리거 ----------------------------------------------------------------------


def test_news_trigger_enqueues_analyze_and_discover_for_novel_news(conn, project):
    deps = trigger_deps(project, conn)
    _news(conn, [("삼성전자 대형 수주", ["005930"]), ("금리 동결", [])])
    out = _news_trigger(deps, conn)
    assert out["novel"] == 2 and len(out["queued"]) == 1
    q = _queued(conn)
    kinds = sorted(k for k, _, _ in q)
    assert kinds == ["analyze_ticker", "discover"]
    an = next(p for k, p, s in q if k == "analyze_ticker")
    assert an["ticker"] == "005930" and an["dry_run"] is False
    assert all(s == "news" for _, _, s in q)
    assert (
        int(conn.execute("SELECT COUNT(*) FROM job_requests WHERE priority=5").fetchone()[0]) == 1
    )
    assert control.get_flag(conn, "news_trigger_at:KR") == NOW.isoformat()
    assert control.get_flag(conn, "discover_count:KR:2026-10-24") == 1


def test_news_trigger_no_session_when_only_similar_news(conn, project):
    deps = trigger_deps(project, conn)
    _news(conn, [("삼성전자 대형 수주", ["005930"])], received=NOW - timedelta(hours=3))
    _news_trigger(deps, conn)
    before = len(_queued(conn))
    # 같은 사건의 다른 매체 기사만 새로 들어옴 → novel 0 → 추가 세션 없음
    _news(conn, [("삼성전자, 대형 수주 - 매체B", ["005930"])])
    later = NOW + timedelta(minutes=30)
    deps = trigger_deps(project, conn, now=later)
    out = _news_trigger(deps, conn)
    assert out["novel"] == 0 and out["queued"] == [] and out["discover"] is None
    assert len(_queued(conn)) == before
    assert conn.execute("SELECT novel FROM news_items WHERE title LIKE '%매체B'").fetchone()[0] == 0


def test_news_trigger_reevaluates_when_today_decision_exists(conn, project):
    deps = trigger_deps(project, conn)
    conn.execute(
        "INSERT INTO decisions(date, market, ticker, action, data_json, created_at) "
        "VALUES ('2026-10-24','KR','005930','BUY','{}','2026-10-24T00:00:00+00:00')"
    )
    _news(conn, [("삼성전자 실적 발표", ["005930"])])
    out = _news_trigger(deps, conn)
    q = _queued(conn)
    re = next(p for k, p, _ in q if k == "reevaluate")
    assert re["ticker"] == "005930" and out["queued"][0]["ticker"] == "005930"
    trig = conn.execute("SELECT kind, detail, price FROM triggers").fetchone()
    assert trig["kind"] == "news" and trig["price"] is None
    assert "삼성전자 실적 발표" in trig["detail"]


def test_news_trigger_skips_outside_window_or_paused_but_marks_novel(conn, project):
    deps = trigger_deps(project, conn)
    control.set_flag(conn, "paused", True)
    _news(conn, [("장중 기사 둘", ["000660"])])
    out = _news_trigger(deps, conn)
    assert out["novel"] == 1 and out["skipped"] == "일시정지" and _queued(conn) == []
    assert conn.execute("SELECT novel FROM news_items").fetchone()[0] == 1
    control.set_flag(conn, "paused", False)
    later = NOW + timedelta(minutes=30)
    deps = trigger_deps(project, conn, now=later)
    deps.settings.discovery.intraday.enabled = False
    _news(conn, [("SK하이닉스 공장 화재", ["000660"])], received=later)
    assert "enabled" in _news_trigger(deps, conn)["skipped"]
    deps.settings.discovery.intraday.enabled = True
    night = datetime(2026, 10, 24, 14, 0, tzinfo=UTC)  # 23:00 KST
    deps = trigger_deps(project, conn, now=night, settings=deps.settings, in_window=False)
    _news(conn, [("야간 기사", ["005930"])], received=night)
    out3 = _news_trigger(deps, conn)
    assert out3["novel"] == 1 and "장중 창" in out3["skipped"] and _queued(conn) == []


def test_news_trigger_respects_discover_cap(conn, project):
    deps = trigger_deps(project, conn)
    deps.settings.discovery.intraday.discover_cap = 1
    control.set_flag(conn, "discover_count:KR:2026-10-24", 1)
    _news(conn, [("금리 동결", [])])
    out = _news_trigger(deps, conn)
    assert out["discover"] == "상한 1" and _queued(conn) == []


def test_news_trigger_held_long_horizon_needs_three_novel_items(conn, project):
    """Phase 23 T: 보유 종목은 프로필 news_min_novel(장기 3건)을 넘어야 재분석 요청."""
    acct = BrokerAccount(id="paper-main", env="paper", cano="0", markets=["KR"])
    pos = Position(
        ticker="005930", market="KR", qty=1, avg_price=Decimal("70000"),
        current_price=Decimal("71000"), eval_amount=Decimal("71000"), pnl_pct=1.4,
    )  # fmt: skip
    deps = trigger_deps(project, conn, brokers=[FakeBroker(acct, positions=[pos])])
    conn.execute(
        "INSERT INTO decisions(date, market, ticker, action, data_json, created_at) "
        "VALUES ('2026-10-24','KR','005930','BUY','{\"horizon_days\": 60}',"
        "'2026-10-24T00:00:00+00:00')"
    )
    _news(conn, [("삼성전자 신규 수주", ["005930"])])
    out = _news_trigger(deps, conn)
    assert out["queued"] == [] and out["below_profile"] == [
        {"ticker": "005930", "news": 1, "need": 3}
    ]
    assert [k for k, _, _ in _queued(conn)] == ["discover"]


def test_news_trigger_updates_flag_and_novel_even_when_skipped(conn, project):
    """Review Focus 2: 꺼짐·일시정지·창 밖이어도 `news_trigger_at` 갱신·novel 집계는 먼저."""
    deps = trigger_deps(project, conn)
    deps.settings.discovery.intraday.enabled = False
    _news(conn, [("삼성전자 기사", ["005930"])])
    out = _news_trigger(deps, conn)
    assert out["novel"] == 1 and out["skipped"] == "discovery.intraday.enabled=false"
    assert control.get_flag(conn, "news_trigger_at:KR") == NOW.isoformat()
    deps.settings.discovery.intraday.enabled = True


def test_news_trigger_discover_throttled_still_marks_novel(conn, project, monkeypatch):
    """Review Focus 2: 감속이어도 `news_trigger_at`·novel 은 갱신, discover 는 큐에 안 넣는다."""
    from trader.kernel.session import usage

    monkeypatch.delenv("FAKE_CLAUDE", raising=False)
    monkeypatch.setattr(usage, "session_allowance", lambda *a, **k: (False, "사용량 감속 테스트"))
    deps = trigger_deps(project, conn)
    _news(conn, [("금리 동결", [])])
    out = _news_trigger(deps, conn)
    assert out["novel"] == 1 and out["discover"].startswith("사용량 감속")
    assert control.get_flag(conn, "news_trigger_at:KR") == NOW.isoformat()
    assert "discover" not in [k for k, _, _ in _queued(conn)]


def test_discover_cap_only_consumed_by_successful_enqueue(conn, project):
    """Review Focus 3: 이미 discover 가 대기 중이면 \"pending\" 이고 카운트는 그대로."""
    deps = trigger_deps(project, conn)
    _news(conn, [("금리 동결", [])])
    assert isinstance(_news_trigger(deps, conn)["discover"], int)
    cap_key = "discover_count:KR:2026-10-24"
    assert control.get_flag(conn, cap_key) == 1
    later = NOW + timedelta(minutes=30)
    deps = trigger_deps(project, conn, now=later, settings=deps.settings)
    _news(conn, [("환율 급등", [])], received=later)
    out = _news_trigger(deps, conn)
    assert out["discover"] == "pending" and control.get_flag(conn, cap_key) == 1


def test_news_reanalyze_not_queued_while_throttled(conn, project, monkeypatch):
    """감속 중엔 뉴스 재분석(analyze_ticker)을 큐에 넣지 않는다 — 실행 때 막혀 '오류'로
    쌓이던 문제."""
    deps = trigger_deps(project, conn)
    monkeypatch.setattr(
        triggers, "session_allowed", lambda d, c, kind: (kind != "analyze", "7일 84%")
    )
    uni = SimpleNamespace(kr=["005930"], us=[])
    rid = triggers.news_reanalyze(
        deps, conn, "KR", "005930", [{"title": "합성"}], date(2026, 10, 28), uni
    )
    assert rid is None
    assert conn.execute("SELECT COUNT(*) FROM job_requests").fetchone()[0] == 0
    monkeypatch.setattr(triggers, "session_allowed", lambda d, c, kind: (True, None))
    rid = triggers.news_reanalyze(
        deps, conn, "KR", "005930", [{"title": "합성"}], date(2026, 10, 28), uni
    )
    assert isinstance(rid, int)  # 감속이 아니면 큐에 들어간다


# --- 공시 트리거 ----------------------------------------------------------------------


class FakeDart:
    def __init__(self, rows):
        self.rows = rows

    def market_disclosures(self, day, pages=3):
        return self.rows


def _row(code, name, no):
    return {
        "stock_code": code,
        "corp_name": "가나",
        "report_nm": name,
        "rcept_no": no,
        "rcept_dt": "20261024",
    }


def _disc(deps, conn, rows):
    return triggers.disclosure_trigger(
        deps, conn, "KR", UNI, dart=FakeDart(rows), edgar=SimpleNamespace()
    )


def test_critical_disclosure_bypasses_min_novel(conn, project):
    """보유가 아니어도 유니버스 종목의 critical 공시 1건이면 바로 analyze_ticker 요청."""
    deps = trigger_deps(project, conn, notifier=Sent())
    rows = [_row("005930", "주권매매거래정지", "1"), _row("000660", "기업설명회", "2")]
    out = _disc(deps, conn, rows)
    assert out["inserted"] == 2 and out["critical_new"] == 1
    assert [p["ticker"] for k, p, s in _queued(conn) if k == "analyze_ticker"] == ["005930"]
    assert deps.notifier.sent[-1].startswith("[중요 공시] KR")
    assert "재분석 요청 #" in deps.notifier.sent[-1]


def test_critical_disclosure_alerts_once(conn, project):
    deps = trigger_deps(project, conn, notifier=Sent())
    _disc(deps, conn, [_row("005930", "감자결정", "1")])
    n = len(deps.notifier.sent)
    out = _disc(deps, conn, [_row("005930", "감자결정", "1")])
    assert len(deps.notifier.sent) == n and "critical_new" not in out
    assert control.get_flag(conn, "disclosure_seen:KR")


def test_critical_disclosure_paused_alerts_only(conn, project):
    control.set_flag(conn, "paused", True)
    deps = trigger_deps(project, conn, notifier=Sent())
    _disc(deps, conn, [_row("005930", "상장폐지", "1")])
    assert _queued(conn) == [] and "알림만" in deps.notifier.sent[-1]


# --- post_ingest 훅 ------------------------------------------------------------------


def test_post_ingest_runs_events_then_triggers_and_isolates_errors(conn, project, monkeypatch):
    deps = trigger_deps(project, conn, notifier=Sent())
    monkeypatch.setattr(
        triggers, "disclosure_trigger", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("x"))
    )
    monkeypatch.setattr(ingest_api, "load_universe", lambda: UNI)
    _news(conn, [("삼성전자 대형 수주", ["005930"])])
    out = triggers.post_ingest(
        conn=conn, markets=["KR"], now=NOW, notifier=deps.notifier, deps=deps
    )
    assert set(out) == {"events:KR", "disclosure:KR", "news:KR"}
    assert out["disclosure:KR"] == {"error": "RuntimeError: x"}
    assert out["news:KR"]["novel"] == 1


def test_post_ingest_without_deps_only_events(conn):
    out = triggers.post_ingest(conn=conn, markets=["KR"], now=NOW)
    assert set(out) == {"events:KR"}


def test_discover_request_wires_inputs_research_port_to_generated_payload(
    conn, project, monkeypatch
):
    from types import SimpleNamespace

    from trader.features.ingest.ports import InputsPortImpl
    from trader.features.ingest.settings import SourcesConfig
    from trader.kernel.config import Secrets
    from trader.kernel.session.run_store import market_dir, read_checkpoint

    deps = trigger_deps(project, conn, notifier=Sent())
    calls = []

    def index(symbol, *, asof):
        calls.append(symbol)
        return {"value": Decimal("4200.50"), "date": "2026-10-07"}

    market = SimpleNamespace(get_index_us_daily=index)
    monkeypatch.setattr(deps.ports["accounts"], "market_client", lambda: market)
    cfg = SourcesConfig.model_validate({"macro": {"fred": False, "ecos": False}})
    deps.ports["inputs"] = InputsPortImpl(Secrets(), cfg, deps.ports)
    monkeypatch.setattr(ingest_api, "load_universe", lambda: UNI)
    monkeypatch.setattr(
        triggers, "run_session", lambda *a, **kw: SimpleNamespace(ok=True, error=None)
    )
    _news(conn, [("시장 발굴 뉴스", [])])
    ingest_api.novel_items(conn, "KR", since=NOW - timedelta(hours=6))
    result = triggers.discover_request(deps, {"market": "KR"}, progress=None)
    assert result["novel"] == 1
    generated = read_checkpoint(market_dir(NOW.date(), "KR", deps.state), "_discovery_inputs")[
        "data"
    ]
    for key, symbol in (("sp500", "SPX"), ("nasdaq", "COMP")):
        assert generated["macro"][key]["data"]["value"] == "4200.50"
        coverage = generated["source_coverage"]["macro"][key]
        assert coverage["status"] == "present" and coverage["source"] == "KIS"
        assert coverage["series_id"] == symbol
    assert calls == ["SPX", "COMP"]
