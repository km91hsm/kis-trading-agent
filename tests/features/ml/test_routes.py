"""ml 웹 자원(토큰 없음, 읽기 전용): `/features/latest`·`/features/history`(피처 스토어),
`/ml/models`·`/ml/models/{version}`(레지스트리).

옛 `tests/web/test_features_api.py` 6건·`test_ml.py` 1건(+ 빈 레지스트리 placeholder) 이식."""

from __future__ import annotations

from datetime import UTC, date, datetime

import pandas as pd
import yaml

from tests.features.conftest import web_client
from trader.features.ingest.api import SectorMap
from trader.features.ml import features, registry
from trader.features.ml.feature import FEATURE

NOW = datetime(2026, 10, 17, tzinfo=UTC)
SECTORS = SectorMap(sectors=["반도체", "기타"], kr={"005930": "반도체", "000660": "반도체"})


def _client(project):
    return web_client(project, [FEATURE], now=NOW)


def _rows(day: date, tickers: dict[str, float | None]) -> pd.DataFrame:
    n = len(tickers)
    return pd.DataFrame(
        {
            "date": [day] * n,
            "ticker": list(tickers),
            "market": ["KR"] * n,
            "close": [100.0 + i for i in range(n)],
            "volume": [1000] * n,
            "roe": list(tickers.values()),
            "roc_10": [0.01] * n,
        }
    )


def _seed(project):
    (project / "config" / "sectors.yaml").write_text(
        yaml.safe_dump(SECTORS.model_dump(), allow_unicode=True), encoding="utf-8"
    )
    (project / "config" / "universe.yaml").write_text(
        yaml.safe_dump({"kr": ["005930", "000660"]}), encoding="utf-8"
    )
    for day in (date(2026, 1, 5), date(2026, 1, 6), date(2026, 1, 7)):
        features.write_rows(
            _rows(day, {"005930": 0.1, "000660": None}), project / "state", sectors=SECTORS
        )


def test_latest_returns_rows_with_sector(project):
    c = _client(project)
    _seed(project)
    r = c.get("/api/features/latest?market=KR")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["market"] == "KR" and body["asof"] == "2026-01-07"
    assert "roe_vs_sector" in body["columns"] and "sector" in body["columns"]
    rows = {row["ticker"]: row for row in body["rows"]}
    assert set(rows) == {"005930", "000660"}
    assert rows["005930"]["sector"] == "반도체" and rows["005930"]["date"] == "2026-01-07"
    assert rows["000660"]["roe"] is None  # NaN → null (JSON 에 NaN 금지)


def test_latest_rejects_unknown_market_and_empty_store(project):
    c = _client(project)
    assert c.get("/api/features/latest?market=JP").status_code == 400
    r = c.get("/api/features/latest?market=KR")
    assert r.status_code == 200 and r.json()["rows"] == [] and r.json()["asof"] is None


def test_history_filters_ticker_and_days(project):
    c = _client(project)
    _seed(project)
    r = c.get("/api/features/history?market=KR&ticker=005930&days=5")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ticker"] == "005930" and [x["date"] for x in body["rows"]] == [
        "2026-01-05",
        "2026-01-06",
        "2026-01-07",
    ]
    assert all(x["ticker"] == "005930" for x in body["rows"])
    r = c.get("/api/features/history?market=KR&ticker=000660&days=5")
    assert r.json()["rows"][0]["roe"] is None


def test_history_rejects_bad_ticker_and_days(project):
    c = _client(project)
    assert c.get("/api/features/history?market=KR&ticker=../x&days=10").status_code == 400
    assert c.get("/api/features/history?market=KR&ticker=005930&days=1").status_code == 422
    assert c.get("/api/features/history?market=KR&ticker=005930&days=5000").status_code == 422
    assert c.get("/api/features/history?market=XX&ticker=005930").status_code == 400


def test_history_unknown_ticker_is_404_not_full_scan(project):
    """유니버스에 없는 종목은 파일을 다 읽지 않고 404 (토큰 없는 API 비용 방어)."""
    c = _client(project)
    _seed(project)
    assert c.get("/api/features/history?market=KR&ticker=999999&days=10").status_code == 404
    assert c.get("/api/features/history?market=KR&ticker=005930&days=10").status_code == 200


def test_latest_tolerates_broken_sectors_yaml(project):
    c = _client(project)
    _seed(project)
    (project / "config" / "sectors.yaml").write_text(
        "sectors: [반도체]\nkr: {'005930': 금융}\n", encoding="utf-8"
    )
    r = c.get("/api/features/latest?market=KR")
    assert r.status_code == 200 and r.json()["rows"][0]["sector"] is None


def _save(project, promoted: bool) -> str:
    meta = {
        "features": ["roc_10"],
        "metrics": {"accuracy": 0.41, "alpha": 0.001, "folds": [{"index": 0, "accuracy": 0.41}]},
        "importances": {"roc_10": 0.01},
    }
    v = registry.save("quant", {"fake": "model"}, meta, state=project / "state", now=lambda: NOW)
    if promoted:
        registry.promote("quant", v, state=project / "state", reason="기준 통과")
    return v


def test_api_ml_models_lists_registry(project):
    c = _client(project)
    r = c.get("/api/ml/models").json()
    assert r["models"] == [] and r["active"] is None and "승격" in r["note"]
    v = _save(project, promoted=True)
    r = c.get("/api/ml/models").json()
    assert r["models"][0]["version"] == v and r["models"][0]["promoted"] is True
    assert r["active"]["version"] == v and "importances" not in r["models"][0]  # 목록은 요약
    assert r["models"][0]["metrics"] == {
        "accuracy": 0.41, "baseline": None, "alpha": 0.001, "n_test": None
    }  # fmt: skip
    d = c.get(f"/api/ml/models/{v}").json()
    assert d["importances"] and d["metrics"]["folds"][0]["accuracy"] == 0.41
    assert c.get("/api/ml/models/nope").status_code == 400
    assert c.get("/api/ml/models/20990101_000000").status_code == 404
    assert c.get("/api/ml/models/../x").status_code in (400, 404)


def test_nav_item_and_no_sse_keys():
    assert [(n.screen, n.id, n.label, n.order) for n in FEATURE.nav] == [
        ("data", "features", "지표·모델", 30)
    ]
    assert FEATURE.sse_keys == ()


def test_model_version_trailing_newline_rejected(project):
    assert _client(project).get("/api/ml/models/20990101_000000%0A").status_code == 400
