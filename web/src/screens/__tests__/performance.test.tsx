import { act } from "react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

const m = vi.hoisted(() => {
  const series = { setData: vi.fn(), createPriceLine: vi.fn(() => ({})), removePriceLine: vi.fn() };
  const chart = {
    addSeries: vi.fn(() => series),
    removeSeries: vi.fn(),
    applyOptions: vi.fn(),
    remove: vi.fn(),
    timeScale: vi.fn(() => ({ fitContent: vi.fn() })),
  };
  return { series, chart };
});
vi.mock("lightweight-charts", () => ({
  createChart: vi.fn(() => m.chart),
  createSeriesMarkers: vi.fn(() => ({ setMarkers: vi.fn(), detach: vi.fn() })),
  LineSeries: { type: "Line" },
}));

import { Performance } from "../performance/Performance.tsx";
import { clearResourceCache } from "../../lib/cache";
import { setEventsClient, type EventsClient } from "../../lib/sse";
import { installFakeFetch, type FakeFetch } from "../../test/fakeFetch";
import { flush, render } from "../../test/render";
import {
  fixtureCalibration,
  fixtureCompare,
  fixtureFoScorecard,
  fixtureMemory,
  fixturePerfAccounts,
  fixturePerformance,
  fixtureRlExperiments,
  fixtureRlReport,
  fixtureShadow,
} from "../../test/fixtures/performance";

let ff: FakeFetch | null = null;
const stub: EventsClient = { subscribe: () => () => {}, status: () => ({ connected: true, since: 0 }), close() {} };

beforeEach(() => {
  vi.clearAllMocks();
  clearResourceCache();
  setEventsClient(stub);
});
afterEach(() => {
  ff?.restore();
  ff = null;
});

type Routes = Parameters<typeof installFakeFetch>[0];
function routes(over: Routes = {}): Routes {
  return {
    "GET /api/accounts": fixturePerfAccounts(),
    "GET /api/analytics/performance": fixturePerformance(),
    "GET /api/analytics/performance/compare": fixtureCompare(),
    "GET /api/analytics/calibration": fixtureCalibration(),
    "GET /api/analytics/shadow": fixtureShadow(),
    "GET /api/analytics/rl/report": fixtureRlReport(),
    "GET /api/analytics/rl/experiments": fixtureRlExperiments(),
    "GET /api/analytics/memory": (u: URL) =>
      u.searchParams.get("q") ? fixtureMemory({ query: { ticker: "005930", entry_id: 1, market: "KR" }, warmup: true, breaker: "임베딩 장애" }) : fixtureMemory(),
    "GET /api/analytics/fo/scorecard": fixtureFoScorecard(),
    ...over,
  };
}

type Page = Parameters<typeof Performance>[0]["page"];
async function mount(r: Routes, page: Page = "performance") {
  ff = installFakeFetch(r);
  const out = render(
    <MemoryRouter initialEntries={["/performance"]}>
      <Performance page={page} />
    </MemoryRouter>,
  );
  await flush();
  await flush();
  return out;
}
const section = (id: string) => document.getElementById(id) as HTMLElement | null;
const callsTo = (path: string) => ff!.calls.map((c) => new URL(c.url, "http://localhost")).filter((u) => u.pathname === `/api${path}`);
function button(root: ParentNode, text: string) {
  return Array.from(root.querySelectorAll("button")).find((b) => b.textContent?.trim() === text) as HTMLButtonElement;
}
function type(input: HTMLInputElement, value: string) {
  const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")!.set!;
  act(() => {
    setter.call(input, value);
    input.dispatchEvent(new Event("input", { bubbles: true }));
  });
}

describe("성과 화면", () => {
  test("성과 페이지: 섹션(개요·계좌 비교)은 데이터 전에 바로 그려지고, 로드 뒤 지표·비교 「NAV 없음」·벤치마크 선이 보인다 — 다른 페이지 절은 없고 부르지도 않는다", async () => {
    ff = installFakeFetch(routes());
    render(
      <MemoryRouter>
        <Performance />
      </MemoryRouter>,
    );
    for (const id of ["performance-overview", "compare"]) expect(section(id)).not.toBeNull();
    for (const id of ["calibration", "shadow", "rl", "memory", "fo-scorecard"]) expect(section(id)).toBeNull();
    await flush();
    await flush();
    for (const p of ["/analytics/calibration", "/analytics/shadow", "/analytics/rl/report", "/analytics/rl/experiments", "/analytics/memory"]) expect(callsTo(p)).toHaveLength(0);
    const overview = section("performance-overview")!;
    expect(overview.textContent).toContain("5.23%");
    expect(overview.textContent).toContain("42.00%");
    expect(overview.textContent).toContain("75.00%");
    // 본 NAV 1개 + 벤치마크 2개(quant null 은 건너뜀)
    expect(m.chart.addSeries).toHaveBeenCalledTimes(3);
    // 벤치마크가 있으면 NAV 를 시작=1.0 으로 정규화해 같은 축에(첫 setData 가 본선)
    const navData = (m.series.setData.mock.calls[0] as unknown[])[0] as { value: number }[];
    expect(navData[0].value).toBe(1);
    expect(navData.at(-1)!.value).toBeCloseTo(1.0523);
    expect(overview.textContent).toContain("시작=1.0");
    expect(section("compare")!.textContent).toContain("NAV 없음");
    const perf = callsTo("/analytics/performance")[0];
    expect(perf.search).toBe("?account_id=paper-1&market=KR&days=30");
  });

  test("추가 분석 페이지: 보정·섀도·RL(·파생 성적표 hidden) 절만 — 개요·복기는 부르지 않는다", async () => {
    await mount(routes(), "analysis");
    for (const id of ["calibration", "shadow", "rl", "fo-scorecard"]) expect(section(id)).not.toBeNull();
    for (const id of ["performance-overview", "compare", "memory"]) expect(section(id)).toBeNull();
    expect(callsTo("/analytics/performance")).toHaveLength(0);
    expect(callsTo("/analytics/memory")).toHaveLength(0);
    expect(section("calibration")!.textContent).toContain("표본 부족");
    expect(section("shadow")!.textContent).toContain("표본 부족(n < 20)");
    expect(section("rl")!.textContent).toContain("rl-007");
    // realized_*_sum 은 weight × price_return 합(분수 수익률) → 퍼센트
    expect(section("rl")!.textContent).toContain("실현 합 RL 2.10% · LLM 1.50%");
    // bias 는 signal 평균 − realized_sign 평균(차이값, 퍼센트 아님)
    expect(section("calibration")!.textContent).toContain("+0.12");
    expect(Array.from(document.querySelectorAll(".page-tabs a")).map((e) => e.textContent)).toEqual(["성과", "복기", "추가 분석"]);
  });

  test("기간 「90일」 을 누르면 days=90 으로 다시 부른다", async () => {
    await mount(routes());
    const btn = button(section("performance-overview")!, "90일");
    expect(btn.getAttribute("aria-pressed")).toBe("false");
    act(() => btn.click());
    await flush();
    expect(btn.getAttribute("aria-pressed")).toBe("true");
    expect(callsTo("/analytics/performance").at(-1)!.searchParams.get("days")).toBe("90");
    expect(callsTo("/analytics/performance/compare").at(-1)!.searchParams.get("days")).toBe("90");
  });

  test("시장 선택을 US 로 바꾸면 market=US 로 다시 부른다", async () => {
    await mount(routes());
    const select = section("performance-overview")!.querySelector('select[aria-label="시장"]') as HTMLSelectElement;
    const setter = Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype, "value")!.set!;
    act(() => {
      setter.call(select, "US");
      select.dispatchEvent(new Event("change", { bubbles: true }));
    });
    await flush();
    expect(callsTo("/analytics/performance").at(-1)!.searchParams.get("market")).toBe("US");
  });

  test("복기 메모리: 처음엔 q 없이, 「005930」 검색하면 q=005930 으로 부르고 warmup·breaker 를 보인다", async () => {
    await mount(routes(), "memory");
    expect(section("performance-overview")).toBeNull();
    const first = callsTo("/analytics/memory")[0];
    expect(first.searchParams.has("q")).toBe(false);
    expect(first.searchParams.get("limit")).toBe("20");
    const mem = section("memory")!;
    expect(mem.textContent).toContain("반도체 업황 반등 기대");
    type(mem.querySelector('input[aria-label="종목 검색"]') as HTMLInputElement, "005930");
    act(() => button(mem, "검색").click());
    await flush();
    await flush();
    const last = callsTo("/analytics/memory").at(-1)!;
    expect(last.searchParams.get("q")).toBe("005930");
    expect(mem.textContent).toContain("워밍업");
    expect(mem.textContent).toContain("임베딩 장애");
  });

  test("벤치마크가 없으면 NAV 는 금액 그대로", async () => {
    const none = { one_over_n: { return: null, series: [] }, buy_and_hold: { return: null, series: [] }, quant: null, benchmark_universe: [] };
    await mount(routes({ "GET /api/analytics/performance": fixturePerformance({ benchmarks: none }) }));
    expect(m.chart.addSeries).toHaveBeenCalledTimes(1);
    const navData = (m.series.setData.mock.calls[0] as unknown[])[0] as { value: number }[];
    expect(navData[0].value).toBe(10_000_000);
  });

  test("fo 계좌가 없으면 파생 성적표 절은 hidden 이고 부르지 않는다", async () => {
    await mount(routes(), "analysis");
    expect(section("fo-scorecard")!.hidden).toBe(true);
    expect(callsTo("/analytics/fo/scorecard")).toHaveLength(0);
  });

  test("fo 계좌가 있으면 파생 성적표를 보인다", async () => {
    await mount(routes({ "GET /api/accounts": fixturePerfAccounts(true) }), "analysis");
    const fo = section("fo-scorecard")!;
    expect(fo.hidden).toBe(false);
    expect(fo.textContent).toContain("55.56%");
    expect(callsTo("/analytics/fo/scorecard")).toHaveLength(1);
  });

  test("한 절의 오류는 그 절에 그대로 보인다", async () => {
    await mount(
      routes({
        "GET /api/analytics/calibration": () => new Response(JSON.stringify({ detail: "보정 계산 실패" }), { status: 500, headers: { "Content-Type": "application/json" } }),
      }),
      "analysis",
    );
    const cal = section("calibration")!;
    expect(cal.textContent).toContain("불러오지 못했습니다");
    expect(cal.textContent).toContain("보정 계산 실패");
    expect(section("shadow")!.textContent).toContain("표본 부족(n < 20)");
  });
});
