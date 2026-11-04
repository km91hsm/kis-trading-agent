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
