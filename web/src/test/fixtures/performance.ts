// 「성과」 화면(Task 11) 테스트 고정값 — 백엔드 응답 모양 그대로(evaluation/analytics_routes.py·analytics_view.py,
// fo/analytics_routes.py). 값은 모두 합성(paper-1·paper-fo·005930·AAPL).
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
    paused: false,
    nav: "10000000",
    day_pnl: "0",
    positions_count: 0,
    pending_approvals: 0,
    ...over,
  };
}

/** 기본은 현물 paper-1 하나. `withFo` 면 모의 파생 paper-fo 를 더한다. */
export function fixturePerfAccounts(withFo = false) {
  return withFo ? [account(), account({ id: "paper-fo", lane: "fo", markets: ["KR"] })] : [account()];
}

const metrics = (over: Over = {}) => ({ return: 0.0523, mdd: -0.081, sortino: 1.234, days: 30, start: "2026-10-06", end: "2026-11-05", ...over });

export function fixturePerformance(over: Over = {}) {
  return {
    account_id: "paper-1",
    market: "KR",
    days: 30,
    nav: [
      { time: "2026-11-03", value: "10000000" },
      { time: "2026-11-04", value: "10100000" },
      { time: "2026-11-05", value: "10523000" },
    ],
    metrics: metrics(),
    benchmarks: {
      one_over_n: { return: 0.031, series: [{ time: "2026-11-03", value: 1 }, { time: "2026-11-05", value: 1.031 }] },
      buy_and_hold: { return: 0.012, series: [{ time: "2026-11-03", value: 1 }, { time: "2026-11-05", value: 1.012 }] },
      quant: null,
      benchmark_universe: ["005930"],
    },
    cost_sensitivity: {},
    turnover: 0.42,
    decision_distribution: { buy: 3, hold: 5, sell: 1 },
    convergence_rate: 0.75,
    ...over,
  };
}

/** paper-1 KR 은 지표 있음, paper-1 US 는 NAV 없음(metrics null). */
export function fixtureCompare(over: Over = {}) {
  return {
    days: 30,
    rows: [
      { account_id: "paper-1", env: "paper", lane: "spot", market: "KR", currency: "KRW", metrics: metrics() },
      { account_id: "paper-1", env: "paper", lane: "spot", market: "US", currency: "USD", metrics: null },
    ],
    ...over,
  };
}

/** 최신 run 은 보고서 있음(역할 2개), 이전 run 은 표본 부족(report null). */
export function fixtureCalibration() {
  return {
    runs: [
      {
        id: 12,
        created_at: "2026-11-05T00:00:00+00:00",
        n_decisions: 40,
        active: true,
        report: {
          asof: "2026-11-05",
          n_decisions: 40,
          active: true,
          min_decisions: 30,
          roles: {
            bull: { hit_rate: 0.6, n: 20, neighbor_hit_rate: 0.55, neighbor_n: 10, bias: 0.12 },
            bear: { hit_rate: 0.45, n: 20, neighbor_hit_rate: null, neighbor_n: 0, bias: -0.05 },
          },
          decisions: { hit_rate: 0.52, n: 40 },
        },
      },
      { id: 11, created_at: "2026-11-04T00:00:00+00:00", n_decisions: 8, active: false, report: null },
    ],
  };
}

const stat = (n: number, mean: number | null, lo: number | null, hi: number | null) => ({ n, mean_diff: mean, ci_low: lo, ci_high: hi });

export function fixtureShadow(over: Over = {}) {
  return {
    market: "KR",
    min_pairs: 20,
    pairs: {
      candidates_1n: stat(25, 0.004, 0.001, 0.007),
      quant_topn: stat(5, -0.002, null, null),
    },
    cash_effect: null,
    recent: [{ date: "2026-11-04", llm: 0.01, candidates_1n: 0.006, quant_topn: null }],
    ...over,
  };
}

const experiment = (over: Over = {}) => ({
  version: "rl-007",
  saved_at: "2026-11-01T00:00:00+00:00",
  backend: "sb3",
  source: "synthetic",
  steps: 100000,
  seeds: 3,
  promoted: true,
  promote_reason: "샤프 개선",
  summary: {},
  ...over,
});

export function fixtureRlExperiments() {
  return { experiments: [experiment(), experiment({ version: "rl-006", promoted: false, promote_reason: null })], active: experiment() };
}

export function fixtureRlReport() {
  return {
    weeks: 8,
    account_id: "paper-1",
    market: "KR",
    model: experiment(),
    shadow: { n: 14, applied: 3, avg_llm_weight: 0.08, avg_proposed_weight: 0.06, matured: 10, realized_rl_sum: 0.021, realized_llm_sum: 0.015 },
  };
}

function memoryEntry(over: Over = {}) {
  return {
    id: 1,
    ticker: "005930",
    market: "KR",
    account_id: "paper-1",
    decision_id: 101,
    round: 1,
    decided_at: "2026-11-01T00:00:00+00:00",
    situation_summary: "반도체 업황 반등 기대",
    decision: { action: "buy" },
    horizon_days: 5,
    reflection: null,
    outcome_at: "2026-11-05T00:00:00+00:00",
    tier: "short",
    importance: 0.7,
    created_at: "2026-11-01T00:00:00+00:00",
    price_return: 0.034,
    held_at_decision: false,
    direction_score: 1,
    avoided_loss: null,
    realized_pnl_pct: null,
    post_fill_return: null,
    skipped_debate: false,
    ...over,
  };
}

/** q 없음 모양. q 있음은 `fixtureMemory({ query: {...}, warmup: true, breaker: null })`. */
export function fixtureMemory(over: Over = {}) {
  return {
    query: null,
    entries: [memoryEntry(), memoryEntry({ id: 2, ticker: "AAPL", market: "US", decision: {}, price_return: null, situation_summary: null })],
    ...over,
  };
}

export function fixtureFoScorecard() {
  return { decisions: 12, matured: 9, hit_rate: 0.5556, contract_pnl: "1250000" };
}
