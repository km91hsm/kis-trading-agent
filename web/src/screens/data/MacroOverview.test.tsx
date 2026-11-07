import { act } from "react";
import { afterEach, beforeEach, expect, test } from "vitest";
import { clearResourceCache } from "../../lib/cache";
import { setEventsClient } from "../../lib/sse";
import { installFakeFetch, type FakeFetch } from "../../test/fakeFetch";
import { flush, render } from "../../test/render";
import { MacroOverview } from "./MacroOverview";

let ff: FakeFetch | undefined;
beforeEach(() => {
  clearResourceCache();
  setEventsClient({ subscribe: () => () => {}, status: () => ({ connected: true, since: 0 }), close() {} });
});
afterEach(() => ff?.restore());

const item = (key: string, label: string, extra = {}) => ({
  key, label, instrument: label, unit: "pt", value: "6000.50", status: "available", reason: null,
  source: "FRED", series_id: "SP500", source_metadata: "recorded", proxy: false, proxy_reason: null,
  observed_date: "2026-11-06", observed_at: null, received_at: "2026-11-07T06:00:00+00:00",
  stored_at: "2026-11-07T06:00:00+00:00", stored_at_basis: "file_modified_at",
  change_pct: null, change_reason: "원천 등락률 미제공 · 저장값 간 임의 계산 안 함", live_verified: false,
  freshness: { status: "unknown", observation_age_days: 1, reason: "일별 저장 관측값 · 발표 시각 및 최신성 미확인" },
  ...extra,
});
const snapshot = (market = "KR") => ({
  market, asof: "2026-11-07T06:00:00+00:00", items: [
    item("sp500", "S&P 500", { change_pct: 0.0125, change_reason: null }),
    item("nasdaq", "나스닥 종합", { source: "KIS", series_id: "COMP", value: "20000", change_pct: 0, change_reason: null }),
    item("us10y", "미국채 10년", { unit: "%", value: "4.25", series_id: "DGS10" }),
    item("wti", "WTI 현물", { unit: "USD/배럴", value: "72.5", proxy: true, series_id: "DCOILWTICO", proxy_reason: "WTI 현물 가격 · WTI 선물 대체" }),
    item("dxy", "광의 달러지수", { unit: "2006-01=100", value: "123.4", proxy: true, series_id: "DTWEXBGS", proxy_reason: "연준 광의 달러지수 · ICE DXY 대체" }),
  ], scan: { lookback_days: 31, max_files: 200, files_read: 1, skipped: 0, truncated: false },
});

test("stored observations show units, honest source timing and proxy identity", async () => {
  ff = installFakeFetch({ "GET /api/macro": snapshot() });
  const { container } = render(<MacroOverview market="KR" />);
  await flush();
  expect(container.querySelectorAll(".macro-overview [data-stat]")).toHaveLength(5);
  expect(container.textContent).toContain("6,000.50");
  expect(container.textContent).toContain("4.25 %");
  expect(container.textContent).toContain("72.50 USD/배럴");
  expect(container.textContent).toContain("+1.25%");
  expect(container.textContent).toContain("0.00%");
  expect(container.textContent).toContain("등락 미제공");
  expect(container.textContent).toContain("WTI 선물 대체");
  expect(container.textContent).toContain("ICE DXY 대체");
  expect(container.textContent).toContain("발표 시각 및 최신성 미확인");
  expect(container.textContent).toContain("파일 수정 시각");
  expect(container.textContent).toContain("11.06");
  expect(container.textContent).not.toContain("실시간");
  expect(container.querySelector("details > summary")?.textContent).toBe("출처와 자료 한계");
});

test("missing observations retain five labels and explain absence without showing zero", async () => {
  const response = snapshot();
  response.items = response.items.map((i) => ({ ...i, value: null, status: "unavailable", reason: "저장된 유효 관측값 없음", source: null, series_id: null, observed_date: null, stored_at: null, change_pct: null, change_reason: "원천 등락률 미제공" } as unknown as typeof i));
  ff = installFakeFetch({ "GET /api/macro": response });
  const { container } = render(<MacroOverview market="KR" />);
  await flush();
  expect(container.textContent).toContain("저장된 유효 관측값 없음");
  expect(container.textContent).toContain("나스닥 종합");
  expect(container.textContent).not.toContain("0.00%");
});

test("market change hides previous scope until its own response and refresh only reads", async () => {
  let resolveUs: ((value: unknown) => void) | undefined;
  ff = installFakeFetch({ "GET /api/macro": (url: URL) => url.searchParams.get("market") === "US" ? new Promise((resolve) => { resolveUs = resolve; }) : snapshot() });
  const { container, rerender } = render(<MacroOverview market="KR" />);
  await flush();
  expect(container.textContent).toContain("6,000.50");
  rerender(<MacroOverview market="US" />);
  await flush();
  expect(container.textContent).not.toContain("6,000.50");
  await act(async () => resolveUs?.(snapshot("US")));
  await flush();
  expect(container.textContent).toContain("6,000.50");
  await act(async () => container.querySelector<HTMLButtonElement>("button")!.click());
  await flush();
  expect(ff.calls.every((c) => c.init?.method === "GET")).toBe(true);
  expect(ff.calls.at(-1)?.url).toContain("market=US");
});

test("read failure is visible and scan limit is disclosed", async () => {
  ff = installFakeFetch({ "GET /api/macro": new Response('{"detail":"합성 읽기 실패"}', { status: 500 }) });
  const { container, unmount } = render(<MacroOverview market="KR" />);
  await flush();
  expect(container.textContent).toContain("합성 읽기 실패");
  unmount();
  clearResourceCache();
  ff.restore();
  ff = installFakeFetch({ "GET /api/macro": { ...snapshot(), scan: { ...snapshot().scan, truncated: true, skipped: 2 } } });
  const next = render(<MacroOverview market="KR" />);
  await flush();
  expect(next.container.textContent).toContain("조회 한도");
  expect(next.container.textContent).toContain("2개");
});
