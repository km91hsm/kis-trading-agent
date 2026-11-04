// 옛 `web/src/live.test.ts` 이식
import { describe, expect, it } from "vitest";
import { applyLive, degradeQuotes, isStale, nextDelay, QUOTE_TTL_MS, type QuotesResp } from "../live";

const resp = (over: Partial<QuotesResp> = {}): QuotesResp => ({
  session: "open",
  at: "2026-11-05T01:00:00+00:00",
  quotes: { "005930": { price: "110", change_pct: 0.0153, at: "2026-11-05T01:00:00+00:00", stale: false, source: "ws" } },
  source: "ws",
  stream: true,
  hub_yield: null,
  next_poll_ms: 1000,
  ...over,
});

describe("nextDelay", () => {
  it("nextDelay: hidden 이면 null, 아니면 resp.next_poll_ms 그대로(1000·5000·60000)", () => {
    for (const ms of [1000, 5000, 60000]) {
      expect(nextDelay(resp({ next_poll_ms: ms }), false)).toBe(ms);
      expect(nextDelay(resp({ next_poll_ms: ms }), true)).toBeNull();
    }
  });
});

describe("applyLive", () => {
  const row = { ticker: "005930", qty: 10, avg_price: "100", last_price: "105" };
  it("applyLive: 수량 10·평단 100·현재가 110 → pnl 100, pnl_pct 10, live true; 시세 없으면 last_price 로 live false", () => {
    const [live] = applyLive([row], resp().quotes);
    expect(live).toMatchObject({ ticker: "005930", current_price: 110, pnl: 100, pnl_pct: 10, live: true });
    const [none] = applyLive([row], {});
    expect(none).toMatchObject({ current_price: 105, pnl: 50, pnl_pct: 5, live: false });
    expect(applyLive([row], null)[0].live).toBe(false);
  });
  it("stale·snapshot 시세는 쓰지 않고, 평단 0 이면 pnl_pct null", () => {
    const q = resp().quotes["005930"];
    expect(applyLive([row], { "005930": { ...q, stale: true } })[0]).toMatchObject({ current_price: 105, live: false });
    expect(applyLive([row], { "005930": { ...q, source: "snapshot" } })[0].live).toBe(false);
    expect(applyLive([{ ...row, avg_price: "0" }], resp().quotes)[0].pnl_pct).toBeNull();
    expect(applyLive([{ ...row, last_price: null }], {})[0]).toMatchObject({ current_price: null, pnl: null, pnl_pct: null });
  });
});

describe("degradeQuotes·isStale", () => {
  it("degradeQuotes 는 모든 시세를 stale 로 내리고 원본은 그대로 둔다", () => {
    const r = resp();
    const d = degradeQuotes(r);
    expect(d.quotes["005930"].stale).toBe(true);
    expect(r.quotes["005930"].stale).toBe(false);
  });
  it("isStale: stale 표시이거나 QUOTE_TTL_MS(30초) 넘게 지났거나 시각이 깨졌으면 참", () => {
    expect(QUOTE_TTL_MS).toBe(30000);
    const q = resp().quotes["005930"];
    const t = Date.parse(q.at);
    expect(isStale(q, t + QUOTE_TTL_MS)).toBe(false);
    expect(isStale(q, t + QUOTE_TTL_MS + 1)).toBe(true);
    expect(isStale({ ...q, stale: true }, t)).toBe(true);
    expect(isStale({ ...q, at: "bad" }, t)).toBe(true);
  });
});
