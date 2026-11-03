import math
from datetime import date, timedelta
from decimal import Decimal

from trader.features.evaluation.memory import vector
from trader.features.ingest.api import indicators
from trader.kernel.broker.models import Candle


def test_situation_vector_12_dims_and_nan_safe():
    row = {
        "roc_10": 5.0,
        "rsi_14": 75.0,
        "vol_z_20": 1.5,
        "high_52w_pos": 0.9,
        "atr_14": 2.0,
        "close": 100.0,
        "macd_hist": 0.5,
        "sma_5": 102.0,
        "sma_20": 98.0,
        "flow_foreign_20d": -300,
        "news_count": 12,
        "community_count": None,
    }
    v = vector.situation_vector(row, "KR")
    assert len(v) == 12 == len(vector.DIMS) and all(isinstance(x, float) for x in v)
    assert all(math.isfinite(x) for x in v)
    d = dict(zip(vector.DIMS, v, strict=True))
    assert d["rsi_14"] == 0.5 and d["is_us"] == 0.0 and d["flow_foreign_sign"] == -1.0
    assert abs(d["atr_pct"] - 0.02) < 1e-9 and abs(d["sma_5_rel"] - (100 / 102 - 1)) < 1e-9
    assert d["community_log"] == 0.0 and 0 < d["news_log"] <= 1.0
    assert vector.situation_vector({}, "US")[-1] == 1.0
    assert vector.cosine(v, v) > 0.999 and vector.cosine([0.0] * 12, v) == 0.0
    assert "RSI" in vector.summary(row, "KR") and "52주" in vector.summary(row, "KR")


def test_sma_rel_sign_matches_indicator():
    # 지표 열 sma_5_rel = close/sma − 1: 현재가가 이평 위면 양수 — 벡터 차원도 같은 부호
    candles = [
        Candle(
            date=date(2026, 1, 1) + timedelta(days=i),
            open=Decimal(100 + i),
            high=Decimal(101 + i),
            low=Decimal(99 + i),
            close=Decimal(100 + i),
            volume=1000,
        )
        for i in range(30)
    ]
    ind = indicators.latest(candles)
    assert ind["sma_5_rel"] > 0
    row = {"close": float(candles[-1].close), "sma_5": ind["sma_5"], "sma_20": ind["sma_20"]}
    v = dict(zip(vector.DIMS, vector.situation_vector(row, "KR"), strict=True))
    assert v["sma_5_rel"] > 0 and abs(v["sma_5_rel"] - ind["sma_5_rel"]) < 1e-9
    v2 = dict(
        zip(
            vector.DIMS,
            vector.situation_vector({"close": 110.0, "sma_5": 100.0}, "KR"),
            strict=True,
        )
    )
    assert abs(v2["sma_5_rel"] - 0.1) < 1e-9
