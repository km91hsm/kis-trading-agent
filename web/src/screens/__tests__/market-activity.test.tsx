import { act } from "react";
import { MemoryRouter, useLocation } from "react-router-dom";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { MarketActivity } from "../data/MarketActivity";
import { clearResourceCache } from "../../lib/cache";
import { noteServerTime } from "../../lib/clock";
import { setEventsClient } from "../../lib/sse";
import { installFakeFetch, type FakeFetch } from "../../test/fakeFetch";
import { flush, render } from "../../test/render";

let ff: FakeFetch | undefined;
beforeEach(() => {
  vi.useFakeTimers();
  vi.setSystemTime(new Date("2026-11-07T01:00:00Z"));
  noteServerTime(new Date().toISOString());
  clearResourceCache();
  setEventsClient({ subscribe: () => () => {}, status: () => ({ connected: true, since: 0 }), close() {} });
});
afterEach(() => {
  ff?.restore();
  vi.useRealTimers();
});

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
  expect(container.textContent).toContain("2025.50 (원천값 · 단위 미확인)");
  expect(container.textContent).toContain("20.25 USD");
  expect(container.textContent).not.toContain("2,025.50 USD");
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

test("fresh growth expires after 120 seconds without SSE events while historical amounts and time remain", async () => {
  ff = installFakeFetch({ "GET /api/quotes/activity": { ...snapshot(), stale: false } });
  const { container } = render(<MemoryRouter><MarketActivity /></MemoryRouter>);
  await flush();
  expect(container.textContent).not.toContain("갱신 지연");
  expect(container.textContent).toContain("150.0%");
  await act(async () => vi.advanceTimersByTime(120_000));
  expect(container.textContent).not.toContain("갱신 지연");
  expect(container.textContent).toContain("150.0%");
  await act(async () => vi.advanceTimersByTime(1000));
  expect(container.textContent).toContain("갱신 지연");
  expect(container.textContent).not.toContain("150.0%");
  const aapl = Array.from(container.querySelectorAll("tbody tr")).find((r) => r.textContent?.includes("AAPL"));
  expect(aapl?.textContent).toContain("계산 불가");
  expect(aapl?.textContent).toContain("2025.50 (원천값 · 단위 미확인)");
  expect(container.textContent).toContain("2026-11-07 10:00:00 KST");
  expect(ff.calls).toHaveLength(1);
});

test("an older individual row expires even when the snapshot timestamp is still fresh", async () => {
  vi.setSystemTime(new Date("2026-11-07T01:02:00Z"));
  const fresh = { ...row("MSFT", 0.5), received_at: "2026-11-07T01:02:00Z" };
  ff = installFakeFetch({ "GET /api/quotes/activity": {
    ...snapshot(), received_at: "2026-11-07T01:02:00Z", stale: false,
    rankings: { turnover: [row("AAPL", 1.5), fresh], gain: [], loss: [] },
  } });
  const { container } = render(<MemoryRouter><MarketActivity /></MemoryRouter>);
  await flush();
  expect(container.textContent).toContain("150.0%");
  await act(async () => vi.advanceTimersByTime(1000));
  const rows = Array.from(container.querySelectorAll("tbody tr"));
  expect(rows.find((r) => r.textContent?.includes("AAPL"))?.textContent).toContain("계산 불가");
  expect(rows.find((r) => r.textContent?.includes("MSFT"))?.textContent).toContain("50.0%");
  expect(container.textContent).toContain("갱신 지연");
});

test.each([null, "invalid", "2026-11-07T01:00:01Z"])("untrustworthy received_at %s cannot show current growth", async (received_at) => {
  ff = installFakeFetch({ "GET /api/quotes/activity": {
    ...snapshot(), received_at, stale: false,
    rankings: { turnover: [{ ...row("AAPL", 1.5), received_at }], gain: [], loss: [] },
  } });
  const { container } = render(<MemoryRouter><MarketActivity /></MemoryRouter>);
  await flush();
  expect(container.textContent).toContain("갱신 지연");
  expect(container.textContent).toContain("계산 불가");
  expect(container.textContent).not.toContain("150.0%");
  expect(container.textContent).toContain("2025.50 (원천값 · 단위 미확인)");
});

function LocationProbe() { return <output data-location>{useLocation().search}</output>; }

test("거래대금 목록은 30행 페이지를 URL에 유지하고 필터를 바꾸면 첫 페이지로 간다", async () => {
  ff = installFakeFetch({ "GET /api/quotes/activity": { ...snapshot(), stale: false,
    rankings: { turnover: Array.from({ length: 65 }, (_, i) => row(`STK${i}`, i / 100)), gain: [], loss: [] },
  } });
  const { container } = render(<MemoryRouter initialEntries={["/data?market=US&apage=2"]}><MarketActivity /><LocationProbe /></MemoryRouter>);
  await flush();
  expect(container.querySelectorAll("a[data-activity-ticker]")).toHaveLength(30);
  expect(container.querySelector("a[data-activity-ticker]")?.textContent).toBe("STK30");
  const select = container.querySelector<HTMLSelectElement>('[aria-label="순위 정렬"]')!;
  act(() => { select.value = "growth"; select.dispatchEvent(new Event("change", { bubbles: true })); });
  expect(container.querySelector("[data-location]")?.textContent).not.toContain("apage=");
  expect(container.querySelector("a[data-activity-ticker]")?.textContent).toBe("STK64");
});

test("first observation outside polling window is pending, not failed or delayed", async () => {
  ff = installFakeFetch({ "GET /api/quotes/activity": {
    ...snapshot(), status: "unavailable", received_at: null, poll_window: "closed", collection_state: "waiting_for_open",
    rankings: { turnover: [], gain: [], loss: [] },
    coverage: { ...snapshot().coverage, successful: [], failed: [], pending: ["KRX:turnover", "KRX:gain", "KRX:loss"] },
  } });
  const { container } = render(<MemoryRouter><MarketActivity /></MemoryRouter>);
  await flush();
  expect(container.textContent).toContain("장 시작 후 첫 수집 대기");
  expect(container.textContent).toContain("미수집 3개");
  expect(container.textContent).not.toContain("갱신 지연");
  expect(container.textContent).not.toContain("실패 3개");
});

test("disabled collection is explained without promising automatic collection", async () => {
  ff = installFakeFetch({ "GET /api/quotes/activity": { ...snapshot(), status: "unavailable", received_at: null, collection_state: "disabled", rankings: { turnover: [], gain: [], loss: [] }, coverage: { successful: [], failed: [], pending: [] } } });
  const { container } = render(<MemoryRouter><MarketActivity /></MemoryRouter>);
  await flush();
  expect(container.textContent).toContain("거래대금 수집 꺼짐");
  expect(container.textContent).not.toContain("자동 수집합니다");
  expect(container.textContent).not.toContain("갱신 지연");
});

test("growth sorting and filtering read URL, preserve market and provide ticker chart links", async () => {
  ff = installFakeFetch({ "GET /api/quotes/activity": { ...snapshot(), stale: false,
    rankings: { turnover: [row("AAA", 0.1), row("BBB", 1.5), row("CCC", null)], gain: [], loss: [] },
  } });
  const { container } = render(<MemoryRouter initialEntries={["/data?market=US&sort=growth&growth_only=1"]}><MarketActivity /><LocationProbe /></MemoryRouter>);
  await flush();
  const links = Array.from(container.querySelectorAll<HTMLAnchorElement>('a[data-activity-ticker]'));
  expect(links.map(a => a.textContent)).toEqual(["BBB", "AAA"]);
  expect(links[0].getAttribute("href")).toContain("ticker=BBB");
  expect(links[0].getAttribute("href")).toContain("market=US");
  const select = container.querySelector<HTMLSelectElement>('[aria-label="순위 정렬"]')!;
  act(() => { select.value = "source"; select.dispatchEvent(new Event("change", { bubbles: true })); });
  expect(container.querySelector('[data-location]')?.textContent).not.toContain("sort=growth");
  expect(container.querySelector('[data-location]')?.textContent).toContain("market=US");
});

test("ranking URL survives navigation and unavailable growth explains missing observations", async () => {
  ff = installFakeFetch({ "GET /api/quotes/activity": snapshot() });
  const { container } = render(<MemoryRouter initialEntries={["/data?market=US&ranking=gain"]}><MarketActivity /><LocationProbe /></MemoryRouter>);
  await flush();
  expect(container.textContent).toContain("NVDA");
  expect(container.textContent).not.toContain("AAPL");
  expect(container.textContent).toContain("관측 이력 부족");
  act(() => Array.from(container.querySelectorAll('button')).find(x => x.textContent === "하락률")!.click());
  expect(container.querySelector('[data-location]')?.textContent).toContain("ranking=loss");
  expect(container.textContent).toContain("AMD");
});
