"""환율 원천(`quotes.fx`): KIS 먼저 → 거시(FRED DEXKOUS) → 없음(404). KST 하루 1회 control 캐시.

KIS 환율 원천은 아직 주입점(`fx.kis_fx`, 기본 None)만 있다 — 가짜 원천으로만 시험한다."""

from datetime import UTC, datetime, timedelta

import pytest

from tests.features.conftest import web_client
from trader.features.quotes import api, fx
from trader.features.quotes.feature import FEATURE
from trader.kernel.ledger import control, db

NOW = datetime(2026, 10, 9, 2, 0, tzinfo=UTC)  # 금 11:00 KST


@pytest.fixture
def sources(monkeypatch):
    calls = {"kis": 0, "macro": 0}
    box = {"kis": None, "macro": None}

    def kis():
        calls["kis"] += 1
        return box["kis"]

    def macro():
        calls["macro"] += 1
        return box["macro"]

    monkeypatch.setattr(fx, "kis_fx", kis)
    monkeypatch.setattr(fx, "macro_usdkrw", macro)
    return box, calls


def _conn(project):
    web_client(project, [FEATURE])  # 원장 생성
    return db.connect(project / "state" / "trader.db")


def test_fx_prefers_kis_then_macro_then_404(project, sources):
    box, calls = sources
    box["kis"] = {"rate": "1391.2", "as_of": "2026-10-09T01:59:00+00:00"}
    box["macro"] = {"rate": "1388.5", "as_of": "2026-10-08"}
    c = web_client(project, [FEATURE], now=NOW)
    r = c.get("/api/quotes/fx", params={"pair": "USDKRW"})
    assert r.status_code == 200
    assert r.json() == {
        "pair": "USDKRW",
        "rate": "1391.2",
        "as_of": "2026-10-09T01:59:00+00:00",
        "source": "kis",
    }
    assert calls == {"kis": 1, "macro": 0}

    conn = db.connect(project / "state" / "trader.db")
    control.delete_flag(conn, "fx_cache:USDKRW")
    box["kis"] = None
    r = c.get("/api/quotes/fx")
    assert r.json()["source"] == "macro" and r.json()["rate"] == "1388.5"

    control.delete_flag(conn, "fx_cache:USDKRW")
    conn.close()
    box["macro"] = None
    assert c.get("/api/quotes/fx").status_code == 404


def test_fx_cached_once_per_kst_day(project, sources):
    box, calls = sources
    box["macro"] = {"rate": "1388.5", "as_of": "2026-10-08"}
    conn = _conn(project)
    first = fx.rate(conn, "USDKRW", now=NOW)
    again = fx.rate(conn, "USDKRW", now=NOW + timedelta(hours=5))
    assert first == again and calls["macro"] == 1
    cached = control.get_flag(conn, "fx_cache:USDKRW")
    assert cached["day"] == "2026-10-09" and cached["source"] == "macro"
    # KST 다음 날(00:00 KST = 15:00 UTC)이면 다시 묻는다
    fx.rate(conn, "USDKRW", now=datetime(2026, 10, 9, 15, 0, tzinfo=UTC))
    assert calls["macro"] == 2
    conn.close()


def test_fx_failing_source_is_skipped(project, monkeypatch):
    def boom():
        raise RuntimeError("KIS 오류")

    monkeypatch.setattr(fx, "kis_fx", boom)
    monkeypatch.setattr(fx, "macro_usdkrw", lambda: {"rate": "1388.5", "as_of": "2026-10-08"})
    conn = _conn(project)
    assert fx.rate(conn, "USDKRW", now=NOW)["source"] == "macro"
    conn.close()


def test_api_fx_rate_shape_and_unknown_pair(project, sources):
    box, _ = sources
    box["macro"] = {"rate": "1388.50", "as_of": "2026-10-08"}
    conn = _conn(project)
    got = api.fx_rate(None, conn, "USDKRW")
    assert got["pair"] == "USDKRW" and got["rate"] == "1388.50" and got["source"] == "macro"
    assert api.fx_rate(None, conn, "EURKRW") is None
    conn.close()


def test_macro_without_fred_key_is_no_source(project, monkeypatch):
    """FRED 키가 없으면 네트워크 없이 None(원천 없음)."""
    monkeypatch.setattr(fx, "load_secrets", lambda: type("S", (), {"FRED_API_KEY": None})())
    assert fx.macro_usdkrw() is None


def test_macro_reads_fred_dexkous(monkeypatch):
    seen = []

    class FakeFred:
        def __init__(self, key, http):
            seen.append(key)

        def latest(self, series_id, *, asof=None):
            seen.append(series_id)
            return {"value": 1388.5, "date": "2026-10-08"}

    monkeypatch.setattr(fx, "load_secrets", lambda: type("S", (), {"FRED_API_KEY": "k"})())
    monkeypatch.setattr(fx.ingest, "Fred", FakeFred)
    got = fx.macro_usdkrw(http_factory=lambda: _NullHttp())
    assert got == {"rate": "1388.5", "as_of": "2026-10-08"} and seen == ["k", "DEXKOUS"]


class _NullHttp:
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False
