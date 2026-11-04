// 「데이터」 화면(Task 12) 테스트 고정값 — 백엔드 응답 모양 그대로(ingest/routes.py·scan/routes.py·ml/routes.py·
// quotes/charts.py·routes.py·names.py·spot/routes.py). 값은 모두 합성(005930 삼성전자·000660·AAPL).
export type Over = Record<string, unknown>;

function newsItem(over: Over = {}) {
  return {
    id: 101,
    market: "KR",
    source: "naver",
    query: "삼성전자",
    title: "삼성전자 HBM 공급 확대",
    url: "https://news.example/101",
    published_at: "2026-11-05T00:30:00+00:00",
    received_at: "2026-11-05T00:31:00+00:00",
    summary: "합성 요약",
    tickers: ["005930"],
    lang: "ko",
    ...over,
  };
}

export function fixtureNews(over: Over = {}) {
  return {
    market: "KR",
    hours: 48,
    total: 2,
    asof: "2026-11-05T00:31:00+00:00",
    items: [newsItem(), newsItem({ id: 102, title: "반도체 업황 점검", url: null, published_at: null, tickers: ["000660"] })],
    ...over,
  };
}

export const fixtureLabels = () => ({ "101": { catalyst: true, themes: ["반도체"] } });
export const fixtureRefs = () => ({ "101": [7, 9] });
export const fixtureNames = () => ({ "KR:005930": "삼성전자" });

export function fixtureEvents() {
  return {
    market: "KR",
    hours: 72,
    asof: "2026-11-05T00:31:00+00:00",
    disclosures: [
      { id: 201, source: "dart", title: "주요사항보고서(유상증자결정)", url: "https://dart.example/201", published_at: "2026-11-05T01:00:00+00:00", received_at: "2026-11-05T01:01:00+00:00", severity: "critical", tickers: ["005930"] },
    ],
  };
}

export const fixtureManual = () => ({ market: "KR", items: [] as unknown[] });

export function fixtureThemes() {
  return {
    market: "KR",
    date: "2026-11-05",
    observed_at: "2026-11-05T00:40:00+00:00",
    top_n: 10,
    themes: [
      {
        theme: "반도체",
        score: 0.8123,
        components: {},
        tickers: [{ ticker: "005930", name: "삼성전자", why: ["뉴스 3건", "거래량 급증"] }],
        members: ["005930", "000660"],
        catalyst_ok: true,
        catalyst_ids: [101],
      },
    ],
  };
}

function featureRow(over: Over = {}) {
  return {
    date: "2026-11-05",
    ticker: "005930",
    market: "KR",
    close: 71500,
    volume: 1000,
    roc_10: 0.0312,
    high_52w_pos: 0.8,
    flow_foreign_20d: 0.1,
    flow_inst_20d: 0,
    flow_indiv_20d: -0.1,
    op_margin: 0.12,
    roe: 0.1,
    debt_ratio: 0.3,
    news_count: 3,
    community_count: 0,
    op_margin_vs_sector: 0.01,
    roe_vs_sector: 0.02,
    debt_ratio_vs_sector: -0.05,
    sector: "반도체",
    ...over,
  };
}

export function fixtureFeaturesLatest() {
  return {
    market: "KR",
    asof: "2026-11-05",
    columns: Object.keys(featureRow()),
    rows: [featureRow(), featureRow({ ticker: "000660", close: 180000, roe: 0.3 }), featureRow({ ticker: "035420", close: 200000, roe: null })],
  };
}

export function fixtureFeaturesHistory() {
  return {
    ticker: "005930",
    market: "KR",
    rows: [featureRow({ date: "2026-11-03", close: 70000 }), featureRow({ date: "2026-11-04", close: 70500 }), featureRow()],
  };
}

function model(over: Over = {}) {
  return {
    name: "lgbm",
    version: "20261101_120000",
    saved_at: "2026-11-01T12:00:00",
    promoted: true,
    promote_reason: "alpha>0",
    promoted_at: "2026-11-01T12:05:00",
    market: "KR",
    rows: 5000,
    train_range: ["2023-01-02", "2026-10-31"],
    backend: "lightgbm",
    metrics: { accuracy: 0.56, baseline: 0.52, alpha: 0.04, n_test: 800 },
    ...over,
  };
}

export function fixtureModels() {
  const m = model();
  return { models: [m, model({ version: "20261020_090000", promoted: false, promoted_at: null })], active: m, note: "판정 보조 신호로만 쓴다" };
}

export const fixtureWatchlist = () => ({ tickers: ["005930", "000660"] });

export function fixtureNameSearch() {
  return [{ market: "KR", ticker: "005930", name: "삼성전자", exchange: null, source: "master" }];
}

export function fixtureCharts(over: Over = {}) {
  return {
    ticker: "005930",
    market: "KR",
    candles: [
      { time: "2026-11-04", open: "70000", high: "71000", low: "69500", close: "70500", volume: 1000 },
      { time: "2026-11-05", open: "70500", high: "72000", low: "70000", close: "71500", volume: 1200 },
    ],
    markers: [{ time: "2026-11-05", kind: "fill", side: "buy", text: "매수 10" }],
    lines: { stop_loss: "65000", take_profit: "80000", date: "2026-11-05" },
    ...over,
  };
}

export function fixtureBook(over: Over = {}) {
  return {
    session: "open",
    ticker: "005930",
    book: {
      asks: [
        ["71600", 120],
        ["71700", 80],
      ],
      bids: [
        ["71500", 150],
        ["71400", 90],
      ],
      total_ask: 200,
      total_bid: 240,
      time: "093000",
      at: "2026-11-05T00:30:00+00:00",
      source: "ws",
    },
    next_poll_ms: 2000,
    ...over,
  };
}

export function fixtureQuotes() {
  return {
    session: "open",
    at: "2026-11-05T00:30:00+00:00",
    quotes: { "005930": { price: "71550", change_pct: 0.01, at: "2026-11-05T00:30:00+00:00", stale: false, source: "ws" } },
    source: "ws",
    next_poll_ms: 60000,
  };
}
