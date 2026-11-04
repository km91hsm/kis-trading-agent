// 「결정 상세」 화면 테스트 고정값 — 백엔드 응답 모양 그대로(spot/decisions_view.detail·trace·outcomes, fo/routes).
// 값은 모두 합성(paper-1·005930·KOSPI200 선물).

export type Over = Record<string, unknown>;

export function fixtureSpotDecisionRow(over: Over = {}) {
  return {
    id: 7,
    date: "2026-11-05",
    market: "KR",
    ticker: "005930",
    action: "BUY",
    target_weight: 0.1,
    order_type: "LIMIT",
    limit_price: "70000",
    stop_loss: "65000",
    take_profit: "80000",
    horizon_days: 10,
    confidence: 0.72,
    rationale: "실적 상향 기대\n외국인 순매수 지속",
    data: {},
    source: "session",
    skipped_debate: 0,
    created_at: "2026-11-05T00:10:00+00:00",
    ...over,
  };
}

export function fixtureSpotDetail(over: Over = {}) {
  return {
    decision: fixtureSpotDecisionRow(),
    rounds: [
      { id: 1, decision_id: 7, round: 1, action: "HOLD", stop_loss: "65000", source: "reeval", skipped_debate: 0, data: {}, created_at: "2026-11-05T02:00:00+00:00" },
      { id: 2, decision_id: 7, round: 2, action: "TRIM", stop_loss: "66000", source: "reeval", skipped_debate: 0, data: {}, created_at: "2026-11-05T04:00:00+00:00" },
    ],
    summary: "승인 요약 — 합성 문단",
    news_refs: [],
    ...over,
  };
}

export function fixtureSpotTrace(over: Over = {}) {
  return {
    decision: fixtureSpotDecisionRow(),
    quote: 70000,
    analysts: [
      { role: "technical", label: "차트", signal: "BULLISH", confidence: 0.7, summary: "s", evidence: ["추세 상승", "거래량 증가"], news_refs: [] },
      { role: "news", label: "뉴스", signal: "NEUTRAL", confidence: 0.5, summary: "s", evidence: [], news_refs: [] },
    ],
    debate: [
      { round: 0, side: "bear", stance: "BEARISH", conviction: 0.4, timing: "WAIT" },
      { round: 0, side: "bull", stance: "BULLISH", conviction: 0.8, timing: "NOW" },
      { round: 1, side: "bull", stance: "BULLISH", conviction: 0.7, timing: "NOW" },
    ],
    research: null,
    gate: [{ account_id: "paper-1", approved: true, qty: 3, reasons: ["한도 안"], checked_at: "2026-11-05T00:11:00+00:00" }],
    orders: [
      { id: 21, account_id: "paper-1", status: "accepted", order_no: "1", side: "BUY", qty: 3, price: "70000", mode: "paper", origin: "decision", message: null, at: "2026-11-05T00:12:00+00:00" },
    ],
    reevaluations: [
      { round: 1, action: "HOLD", stop_loss: "65000", source: "reeval", created_at: "2026-11-05T02:00:00+00:00", gate: [], orders: [] },
    ],
    stop_loss: { gate: [], orders: [] },
    reprice: { gate: [], orders: [] },
    stages: ["00_inputs", "30_decision"],
    ...over,
  };
}

export function fixtureOutcomes(over: Over = {}) {
  return {
    decision: fixtureSpotDecisionRow(),
    outcomes: [
      {
        account_id: "paper-1",
        approved: true,
        adjusted_qty: 3,
        reasons: ["한도 안"],
        checked_at: "2026-11-05T00:11:00+00:00",
        order_status: "filled",
        order_no: "1",
        order_qty: 3,
        order_price: "70000",
        order_mode: "paper",
      },
    ],
    ...over,
  };
}

export function fixtureFoDecisionRow(over: Over = {}) {
  return {
    id: 3,
    date: "2026-11-05",
    account_id: "paper-fo",
    position_id: null,
    round: 0,
    intent: "ENTER",
    direction: "LONG",
    vehicle: "FUTURES",
    target_leverage: 1,
    confidence: 0.6,
    index_at_decision: "350.00",
    stop_pt: "347.00",
    take_profit_pt: "356.00",
    horizon_days: 1,
    rationale: "지수 반등 기대\n변동성 축소",
    vs_spot: null,
    record_only: 0,
    created_at: "2026-11-04T23:30:00+00:00",
    asset: "fo" as const,
    status: "filled",
    request_id: null,
    ...over,
  };
}

export function fixtureFoDetail(over: Over = {}) {
  return { decision: fixtureFoDecisionRow(), data: {}, legs: [], ...over };
}

export function fixtureFoTrace(over: Over = {}) {
  return {
    decision: fixtureFoDecisionRow(),
    stages: ["_fo_analyst_macro", "_fo_decision", "_fo_risk_check_paper-fo"],
    analysts: [{ role: "macro", signal: "BULLISH", confidence: 0.6, summary: "합성 요약" }],
    checkpoint: { intent: "ENTER" },
    checks: { "_fo_risk_check_paper-fo": { approved: true, reasons: ["한도 안"], note: null, status: null, record_only: null, error: null } },
    ...over,
  };
}
