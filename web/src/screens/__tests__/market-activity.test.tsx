import { act } from "react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, expect, test } from "vitest";
import { MarketActivity } from "../data/MarketActivity";
import { clearResourceCache } from "../../lib/cache";
import { setEventsClient } from "../../lib/sse";
import { installFakeFetch, type FakeFetch } from "../../test/fakeFetch";
import { flush, render } from "../../test/render";

let ff: FakeFetch | undefined;
beforeEach(() => {
  clearResourceCache();
  setEventsClient({ subscribe: () => () => {}, status: () => ({ connected: true, since: 0 }), close() {} });
});
afterEach(() => ff?.restore());

const row = (ticker: string, growth: number | null = null) => ({
  ticker, name: ticker, market: "US", exchange: "NAS", currency: "USD", source_rank: 1,
  price: "20.25", change_pct: 0.025, volume: 100, turnover: "2025.50",
  received_at: "2026-11-07T01:00:00Z", source_asof: null, basis: "unknown", stale: false,
  growth: { status: growth === null ? "unavailable" : "available", value: growth, recent_5m: null, previous_5m: null, reason: "insufficient_history" },
});
const snapshot = () => ({
  source: "kis", market: "US", currency: "USD", received_at: "2026-11-07T01:00:00Z",
  source_asof: null, basis: "unknown", status: "partial", stale: true,
  coverage: { expected: ["NAS", "NYS", "AMS"], successful: ["NAS:turnover"], failed: ["NYS:gain"], scope: "bounded_rankings", complete: false, session: "unknown" },
  rankings: { turnover: [row("AAPL", 1.5), row("MSFT")], gain: [row("NVDA")], loss: [row("AMD")] }, errors: [],
});

test("market data shows source and stale/partial truth, preserves currency and unavailable growth, and selects ranking", async () => {
  ff = installFakeFetch({ "GET /api/quotes/activity": snapshot() });
  const { container } = render(<MemoryRouter initialEntries={["/data?market=US"]}><MarketActivity /></MemoryRouter>);
  await flush();
  expect(ff.calls[0].url).toContain("market=US");
  expect(container.textContent).toContain("KIS");
  expect(container.textContent).toContain("일부 범위");
  expect(container.textContent).toContain("갱신 지연");
  expect(container.textContent).toContain("거래 세션 미확인");
  expect(container.textContent).toContain("USD");
  expect(container.textContent).toContain("2,025.50");
  expect(container.textContent).toContain("150.0%");
  expect(container.textContent).toContain("2.50%");
  const msft = Array.from(container.querySelectorAll("tbody tr")).find((r) => r.textContent?.includes("MSFT"));
  expect(msft?.textContent).toContain("계산 불가");
  expect(msft?.textContent).not.toContain("0.0%");
  const gain = Array.from(container.querySelectorAll("button")).find((b) => b.textContent === "상승률")!;
  act(() => gain.click());
  expect(container.textContent).toContain("NVDA");
  expect(container.textContent).not.toContain("AAPL");
});

test("empty collection is unavailable and refresh is a read-only request", async () => {
  ff = installFakeFetch({ "GET /api/quotes/activity": { ...snapshot(), status: "unavailable", received_at: null, rankings: { turnover: [], gain: [], loss: [] } } });
  const { container } = render(<MemoryRouter><MarketActivity /></MemoryRouter>);
  await flush();
  expect(container.textContent).toContain("수집 자료 없음");
  expect(container.textContent).not.toContain("정상 수집");
  await act(async () => Array.from(container.querySelectorAll("button")).find((b) => b.textContent === "순위 새로고침")!.click());
  await flush();
  expect(ff.calls).toHaveLength(2);
  expect(ff.calls.every((c) => c.init?.method === "GET")).toBe(true);
});
