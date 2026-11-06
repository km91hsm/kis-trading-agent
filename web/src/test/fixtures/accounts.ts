// 「계좌·보유」 통합 화면 테스트 고정값 — 백엔드 응답 모양 그대로(accounts/overview.py account_rows·totals).
// 값은 모두 합성(paper-1·real-1·paper-fo).
export type Over = Record<string, unknown>;

function account(over: Over = {}) {
  return {
    id: "paper-1",
    env: "paper",
    lane: "spot",
    markets: ["KR", "US"],
    currency: "KRW",
    mode: "approve",
    mode_note: null,
    mode_source: "yaml",
    paused: false,
    nav: "10000000",
    nav_at: "2026-11-05T01:00:00+00:00",
    cash: "5000000",
    cash_at: "2026-11-05T01:00:00+00:00",
    usd_nav: null,
    day_pnl: "120000",
    positions_count: 2,
    pending_approvals: 0,
    ...over,
  };
}

/** paper-1(모의 현물 KRW)·real-1(실전 현물 auto)·paper-fo(모의 파생). */
export function fixtureAccounts() {
  return [
    account(),
    account({ id: "real-1", env: "real", mode: "auto", mode_note: "LIVE 확인 11-01", nav: "3000000", day_pnl: "-45000", positions_count: 1, pending_approvals: 2 }),
    account({ id: "paper-fo", lane: "fo", markets: ["KR"], mode: "off", nav: "50000000", day_pnl: "0", positions_count: 0 }),
  ];
}

/** fx 있음이 기본; `fixtureOverview({ fx: null })` 로 환율 없음. */
export function fixtureOverview(over: Over = {}) {
  return {
    krw: { nav: "13000000", day_pnl: "75000", accounts: ["paper-1", "real-1"] },
    usd: { nav: "2500.5", day_pnl: "-12.25", accounts: ["paper-1"] },
    fx: { pair: "USD/KRW", rate: "1385.50", as_of: "11월 4일 종가", converted_total: "16464442.75" },
    paused: false,
    at: "2026-11-05T01:00:00+00:00",
    ...over,
  };
}

/** `GET /fo/summary`(fo/routes.py _account_status) — paper-fo 한 계좌, 기록만 상태. */
export function fixtureFoSummary(over: Over = {}) {
  return {
    accounts: [
      {
        id: "paper-fo", env: "paper", mode: "off", mode_note: null, paused: false,
        state: "record_only", enabled: true, vehicles: ["OPTION_BUY"],
        verified: { FUTURES: false, OPTION_BUY: true }, halted: false, pending_entry: false,
        nav_date: "2026-11-05", nav: "50500000", cash: "38500000", margin_total: "12000000",
        add_margin: null, margin_pct: 0.2376, positions: [], decision: null,
        ...over,
      },
    ],
  };  // fmt: skip
}

// ── 계좌별 화면(Task 10) — `/accounts/{id}/positions`(merge {items, partial})·리스크·성과·tax-lots ──
function spotRow(over: Over = {}) {
  return {
    account_id: "paper-1",
    asset: "stock",
    lane: "spot",
    market: "KR",
    ticker: "005930",
    name: "삼성전자",
    qty: 10,
    avg_price: "70000",
    last_price: null,
    currency: "KRW",
    stop: { level: "65100", source: "추적" },
    target: { level: "80000", source: "결정" },
    horizon: "swing",
    weight: 0.07,
    current_price: "71000",
    ...over,
  };
}

/** paper-1: 현물 005930(KR)·AAPL(US) + 단타 000660(KR). `fixturePositions({ partial: true })`. */
export function fixturePositions(over: Over = {}) {
  return {
    items: [
      spotRow(),
      spotRow({ market: "US", ticker: "AAPL", name: "Apple", qty: 2, avg_price: "200.5", currency: "USD", current_price: "210", stop: null, target: null }),
      spotRow({
        lane: "scalp",
        ticker: "000660",
        name: "SK하이닉스",
        qty: 3,
        avg_price: "150000",
        stop: { level: "148500", source: "단타 규칙" },
        target: { level: "153000", source: "단타 규칙" },
        status: "open",
        current_price: undefined,
      }),
    ],
    partial: false,
    ...over,
  };
}

/** paper-fo: 파생 한 행(qty=계약, avg_price=포인트). */
export function fixtureFoPositions(over: Over = {}) {
  return {
    items: [
      {
        account_id: "paper-fo",
        asset: "fo",
        lane: "fo",
        market: "KR",
        ticker: "A0166000",
        name: null,
        qty: 2,
        avg_price: "350.25",
        last_price: null,
        currency: "KRW",
        krw_value: "175125000",
        stop: { level: "345.5", source: "손절 −1.5%" },
        target: { level: "360", source: "결정" },
        kind: "PUT",
        contract_side: "LONG",
        direction: "SHORT",
        vehicle: "OPTION_BUY",
        status: "open",
        position_id: 1,
        last_trade_date: "2026-12-10",
        horizon_until: "2026-11-20",
        at: "2026-11-05T01:00:00+00:00",
      },
    ],
    partial: false,
    ...over,
  };
}

export function fixtureRisk(over: Over = {}) {
  return {
    account_id: "paper-1",
    market: "KR",
    asof: "2026-11-04",
    window: 60,
    n_obs: 60,
    var95: 0.0213,
    cvar95: 0.0342,
    beta: 1.05,
    corr: { tickers: ["005930"], matrix: [[1]] },
    contributions: [
      { ticker: "005930", weight: 0.6, contribution: 0.55 },
      { ticker: "000660", weight: 0.3, contribution: 0.35 },
      { ticker: "035420", weight: 0.08, contribution: 0.07 },
      { ticker: "051910", weight: 0.02, contribution: 0.03 },
    ],
    missing: [],
    ...over,
  };
}

export function fixturePerformance() {
  return {
    account_id: "paper-1",
    market: "KR",
    days: 30,
    nav: [
      { time: "2026-11-03", value: "9950000" },
      { time: "2026-11-04", value: "10000000" },
    ],
    metrics: { return: 0.005, mdd: -0.01, sortino: 1.2, days: 2, start: "2026-11-03", end: "2026-11-04" },
  };
}

export function fixtureFoReport() {
  return {
    account_id: "paper-fo",
    days: 30,
    nav: [
      { date: "2026-11-03", nav: "49800000" },
      { date: "2026-11-04", nav: "50000000" },
    ],
    return: 0.004,
  };
}

export function fixtureTaxLots() {
  return {
    account_id: "paper-1",
    market: "KR",
    year: 2026,
    realized: [
      { ticker: "005930", qty: 2, buy_date: "2026-03-02", sell_date: "2026-09-01", cost: "130000", proceeds: "150000", pnl: "20000", term: "short", wash_sale: false },
      { ticker: "005930", qty: 1, buy_date: "2024-05-02", sell_date: "2026-09-01", cost: "60000", proceeds: "75000", pnl: "15000", term: "long", wash_sale: true },
    ],
    open_lots: [
      { ticker: "005930", qty: 6, price: "69000", bought_at: "2026-10-01T01:00:00+00:00" },
      { ticker: "005930", qty: 4, price: "71500", bought_at: "2026-10-15T01:00:00+00:00" },
      { ticker: "AAPL", qty: 2, price: "200.5", bought_at: "2026-10-02T14:00:00+00:00" },
    ],
    summary: { realized_pnl: "35000", short_pnl: "20000", long_pnl: "15000", wash_sales: 1, unmatched_sell_qty: 0 },
    note: null,
  };
}
