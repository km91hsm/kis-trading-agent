// 「오늘」 화면 테스트 고정값 — 백엔드 응답 모양 그대로(spot/approvals.py row_of·decisions_view·fo/routes·accounts/overview).
// 값은 모두 합성(paper-1·real-1·005930·AAPL).
import { fixtureUsage } from "../fixtures";
import type { UsageResp } from "../../lib/usage";

export type Over = Record<string, unknown>;

export function fixtureAccount(over: Over = {}) {
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
    day_pnl: "0",
    positions_count: 2,
    pending_approvals: 0,
    ...over,
  };
}

export function fixtureApproval(over: Over = {}) {
  return {
    id: 11,
    account_id: "paper-1",
    env: "paper",
    decision: { asset: "spot", id: 7 },
    ticker: "005930",
    side: "BUY",
    qty: 3,
    amount: "210000",
    summary: "실적 상향·외국인 순매수",
    expires_at: "2026-11-05T01:30:00+00:00",
    status: "pending",
    decided_by: null,
    decided_channel: null,
    ...over,
  };
}

export function fixtureSpotDecision(over: Over = {}) {
  return {
    asset: "spot",
    id: 7,
    date: "2026-11-05",
    market: "KR",
    ticker: "005930",
    side: "BUY",
    account_id: "paper-1",
    qty: 3,
    status: "filled",
    at: "2026-11-05T01:00:00+00:00",
    lane: "spot",
    action: "BUY",
    target_weight: 0.05,
    confidence: 0.7,
    source: "analyze",
    ...over,
  };
}

export function fixtureScalpDecision(over: Over = {}) {
  return {
    asset: "spot",
    id: "scalp-12",
    date: "2026-11-05",
    market: "KR",
    ticker: "005930",
    side: "SELL",
    account_id: "paper-1",
    qty: 1,
    status: "accepted",
    at: "2026-11-05T02:00:00+00:00",
    lane: "scalp",
    action: "SELL",
    order_id: 12,
    ...over,
  };
}

export function fixtureFoDecision(over: Over = {}) {
  return {
    asset: "fo",
    id: 3,
    date: "2026-11-05",
    account_id: "paper-1",
    intent: "OPEN",
    status: "filled",
    at: "2026-11-05T00:30:00+00:00",
    market: "KRX_FO",
    lane: "fo",
    round: 1,
    direction: "LONG",
    vehicle: "FUT",
    confidence: 0.6,
    ...over,
  };
}

export function fixtureSpotOrder(over: Over = {}) {
  return {
    asset: "spot",
    id: 21,
    account_id: "paper-1",
    market: "KR",
    ticker: "005930",
    side: "BUY",
    qty: 3,
    price: "70000",
    order_type: "limit",
    status: "filled",
    order_no: "0001",
    origin: "analyze",
    lane: "spot",
    decision_id: 7,
    filled_qty: 3,
    amount: "210000",
    mode: "paper",
    message: null,
    fills: [{ qty: 3, price: "70000", filled_at: "2026-11-05T01:05:00+00:00" }],
    at: "2026-11-05T01:01:00+00:00",
    ...over,
  };
}

export const merged = <T>(items: T[], partial = false) => ({ items, partial });

export function fixtureUsageCaps(caps: UsageResp["caps_by_kind"]): UsageResp {
  return { ...fixtureUsage(), caps_by_kind: caps };
}
