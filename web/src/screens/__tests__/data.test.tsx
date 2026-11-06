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
  return { series, chart, LineSeries: { type: "Line" }, CandlestickSeries: { type: "Candlestick" } };
});
vi.mock("lightweight-charts", () => ({
  createChart: vi.fn(() => m.chart),
  createSeriesMarkers: vi.fn(() => ({ setMarkers: vi.fn(), detach: vi.fn() })),
  LineSeries: m.LineSeries,
  CandlestickSeries: m.CandlestickSeries,
}));

import { Data } from "../data/Data.tsx";
import { tokenStore } from "../../lib/api";
import { clearResourceCache } from "../../lib/cache";
import { setEventsClient, type EventsCallback, type EventsClient, type EventSnapshot } from "../../lib/sse";
import { installFakeFetch, type FakeFetch } from "../../test/fakeFetch";
import { flush, render } from "../../test/render";
import {
  fixtureBook,
  fixtureCharts,
  fixtureEvents,
  fixtureFeaturesHistory,
  fixtureFeaturesLatest,
  fixtureLabels,
  fixtureManual,
  fixtureModels,
  fixtureNames,
  fixtureNameSearch,
  fixtureNews,
  fixtureQuotes,
  fixtureRefs,
  fixtureThemes,
  fixtureWatchlist,
} from "../../test/fixtures/data";

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
  vi.useRealTimers();
});

type Routes = Parameters<typeof installFakeFetch>[0];
function routes(over: Routes = {}): Routes {
  return {
    "GET /api/news": fixtureNews(),
    "GET /api/news/labels": fixtureLabels(),
    "GET /api/news/events": fixtureEvents(),
    "GET /api/news/manual": fixtureManual(),
    "POST /api/news/manual": (_u: URL, init?: RequestInit) => {
      const b = JSON.parse(String(init?.body)) as { market: string; url: string };
      return { market: b.market, received_at: "2026-11-05T01:00:00+00:00", title: "수동 기사", url: b.url, note: "" };
    },
    "GET /api/news/themes": fixtureThemes(),
    "GET /api/names": fixtureNames(),
    "GET /api/names/search": fixtureNameSearch(),
    "GET /api/decisions/spot/news-refs": fixtureRefs(),
    "GET /api/features/latest": fixtureFeaturesLatest(),
    "GET /api/features/history": fixtureFeaturesHistory(),
    "GET /api/ml/models": fixtureModels(),
    "GET /api/settings/watchlist": fixtureWatchlist(),
    "PATCH /api/settings/watchlist": (_u: URL, init?: RequestInit) => JSON.parse(String(init?.body)) as unknown,
    "GET /api/charts": fixtureCharts(),
    "GET /api/quotes/book": fixtureBook(),
    "GET /api/quotes": fixtureQuotes(),
    ...over,
  };
}

type Page = Parameters<typeof Data>[0]["page"];
async function mount(r: Routes, page: Page = "quotes") {
  ff = installFakeFetch(r);
  const out = render(
    <MemoryRouter initialEntries={["/data"]}>
      <Data page={page} />
    </MemoryRouter>,
  );
  await flush();
  await flush();
  return out;
}
const section = (id: string) => document.getElementById(id) as HTMLElement;
const callsTo = (path: string, method = "GET") =>
  ff!.calls.filter((c) => (c.init?.method ?? "GET").toUpperCase() === method).map((c) => new URL(c.url, "http://localhost")).filter((u) => u.pathname === `/api${path}`);
const bodiesTo = (path: string, method: string) =>
  ff!.calls.filter((c) => c.init?.method === method && new URL(c.url, "http://localhost").pathname === `/api${path}`).map((c) => JSON.parse(String(c.init!.body)) as unknown);
function button(root: ParentNode, text: string) {
  return Array.from(root.querySelectorAll("button")).find((b) => b.textContent?.trim() === text) as HTMLButtonElement;
}
function type(el: HTMLInputElement | HTMLTextAreaElement, value: string) {
  const proto = el instanceof HTMLTextAreaElement ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
  const setter = Object.getOwnPropertyDescriptor(proto, "value")!.set!;
  act(() => {
    setter.call(el, value);
    el.dispatchEvent(new Event("input", { bubbles: true }));
  });
}
async function pickSamsung() {
  const q = section("quotes");
  type(q.querySelector('input[aria-label="종목 검색"]') as HTMLInputElement, "삼성");
  act(() => button(q, "검색").click());
  await flush();
  act(() => button(q, "삼성전자 005930").click());
  await flush();
  await flush();
}

describe("데이터 화면", () => {
  test("페이지마다 자기 절만 데이터 전에 바로 그린다 — 시세·차트 / 뉴스·공시 / 테마·감시목록 / 지표·모델", async () => {
    ff = installFakeFetch(routes());
    const pagesOf: [Page, string[]][] = [
      ["quotes", ["quotes"]],
      ["news", ["news"]],
      ["themes", ["themes", "watchlist"]],
      ["features", ["features"]],
    ];
    const all = ["quotes", "news", "themes", "watchlist", "features"];
    for (const [page, ids] of pagesOf) {
      const { unmount } = render(
        <MemoryRouter>
          <Data page={page} />
        </MemoryRouter>,
      );
      for (const id of all) expect(!!section(id), `${page} #${id}`).toBe(ids.includes(id));
      expect(document.querySelector("h1")!.textContent).toContain("데이터");
      await flush();
      unmount();
    }
    // 시세·차트 페이지는 뉴스·지표를 부르지 않는다
    ff.calls.length = 0;
    const { unmount } = render(
      <MemoryRouter>
        <Data page="quotes" />
      </MemoryRouter>,
    );
    await flush();
    await flush();
    for (const p of ["/news", "/news/events", "/features/latest", "/ml/models", "/settings/watchlist"]) expect(callsTo(p)).toHaveLength(0);
    expect(Array.from(document.querySelectorAll(".page-tabs a")).map((e) => e.textContent)).toEqual(["시세·차트", "뉴스·공시", "테마·감시목록", "지표·모델"]);
    unmount();
  });

  test("뉴스: 종목 칩은 이름, 「판정에 쓰임」 은 판정 링크, 라벨·이름·refs 를 2차 조회", async () => {
    await mount(routes(), "news");
    const news = section("news");
    expect(callsTo("/news")[0].search).toBe("?market=KR&hours=48&limit=100");
    expect(callsTo("/news/labels")[0].searchParams.get("ids")).toBe("101,102");
    expect(callsTo("/names")[0].searchParams.get("tickers")).toBe("005930,000660");
    expect(callsTo("/names")[0].searchParams.get("market")).toBe("KR");
    expect(callsTo("/decisions/spot/news-refs")[0].searchParams.get("date")).toMatch(/^\d{4}-\d{2}-\d{2}$/);
    expect(news.textContent).toContain("삼성전자 HBM 공급 확대");
    expect(news.textContent).toContain("삼성전자");
    expect(news.textContent).toContain("촉매");
    const link = Array.from(news.querySelectorAll("a")).find((a) => a.textContent === "판정에 쓰임") as HTMLAnchorElement;
    expect(link.getAttribute("href")).toBe("/decisions/spot/7");
    expect(news.textContent).toContain("데몬이 주기적으로 수집");
    expect(button(news, "지금 수집")).toBeDefined();
    // 이벤트(공시) — 중요도 칩
    expect(news.textContent).toContain("주요사항보고서(유상증자결정)");
    expect(news.querySelector('.chip[data-tone="fail"]')?.textContent).toBe("critical");
  });

  test("「지금 수집」: POST /jobs {kind: news_collect, params: {market}}", async () => {
    tokenStore.set("tok");
    await mount(routes({ "POST /api/jobs": { id: 9, kind: "news_collect", params: { market: "KR" }, status: "queued", label: "뉴스 수집" } }), "news");
    act(() => button(section("news"), "지금 수집").click());
    await act(async () => button(document.querySelector('[role="dialog"]') as HTMLElement, "확인").click());
    await flush();
    expect(bodiesTo("/jobs", "POST")).toEqual([{ kind: "news_collect", params: { market: "KR" } }]);
  });

  test("수동 입력: POST /news/manual {market, text, url}", async () => {
    tokenStore.set("tok");
    await mount(routes(), "news");
    const news = section("news");
    type(news.querySelector('textarea[aria-label="기사 붙여 넣기"]') as HTMLTextAreaElement, "수동 기사\n메모");
    type(news.querySelector('input[aria-label="URL"]') as HTMLInputElement, "https://m.example/1");
    await act(async () => button(news, "저장").click());
    await flush();
    expect(bodiesTo("/news/manual", "POST")).toEqual([{ market: "KR", text: "수동 기사\n메모", url: "https://m.example/1" }]);
    expect(news.textContent).toContain("저장했습니다");
  });

  test("감시목록 PATCH 는 토큰이 필요하다 — 없으면 오류 줄, 있으면 {tickers}", async () => {
    await mount(routes(), "themes");
    const wl = section("watchlist");
    expect(wl.textContent).toContain("005930");
    type(wl.querySelector('input[aria-label="종목 추가"]') as HTMLInputElement, "AAPL");
    act(() => button(wl, "추가").click());
    await act(async () => button(wl, "저장").click());
    await flush();
    expect(callsTo("/settings/watchlist", "PATCH")).toHaveLength(0);
    expect(wl.querySelector('[role="alert"]')?.textContent).toContain("쓰기 토큰이 필요합니다");

    tokenStore.set("tok");
    act(() => (wl.querySelector('button[aria-label="000660 빼기"]') as HTMLButtonElement).click());
    await act(async () => button(wl, "저장").click());
    await flush();
    expect(bodiesTo("/settings/watchlist", "PATCH")).toEqual([{ tickers: ["005930", "AAPL"] }]);
    expect(wl.querySelector('[role="alert"]')).toBeNull();
  });

  test("차트: 종목 검색 → /names/search?q=삼성 → 선택 시 /charts?ticker=005930, 캔들·손절·목표·현재가선·호가", async () => {
    await mount(routes());
    await pickSamsung();
    const search = callsTo("/names/search")[0];
    expect(search.searchParams.get("q")).toBe("삼성");
    expect(search.searchParams.get("market")).toBe("KR");
    expect(callsTo("/charts")[0].search).toBe("?ticker=005930&market=KR&days=120");
    expect((m.chart.addSeries.mock.calls[0] as unknown[])[0]).toBe(m.CandlestickSeries);
    expect(m.series.setData).toHaveBeenLastCalledWith([
      { time: "2026-11-04", open: 70000, high: 71000, low: 69500, close: 70500 },
      { time: "2026-11-05", open: 70500, high: 72000, low: 70000, close: 71500 },
    ]);
    const titles = m.series.createPriceLine.mock.calls.map((c) => ((c as unknown[])[0] as { title: string }).title);
    expect(titles).toEqual(expect.arrayContaining(["손절", "목표", "현재가"]));
    expect(callsTo("/quotes/book")[0].searchParams.get("ticker")).toBe("005930");
    expect(section("quotes").textContent).toContain("71600");
  });

  test("호가는 next_poll_ms 마다 다시 부르고, 언마운트하면 멈춘다", async () => {
    vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout", "setInterval", "clearInterval"] });
    const { unmount } = await mount(routes());
    await pickSamsung();
    expect(callsTo("/quotes/book")).toHaveLength(1);
    await act(async () => vi.advanceTimersByTimeAsync(2000));
    await flush();
    expect(callsTo("/quotes/book")).toHaveLength(2);
    unmount();
    await vi.advanceTimersByTimeAsync(10000);
    expect(callsTo("/quotes/book")).toHaveLength(2);
  });

  test("US 는 호가를 부르지 않는다", async () => {
    await mount(routes({ "GET /api/names/search": [{ market: "US", ticker: "AAPL", name: "Apple", exchange: "NAS", source: "master" }] }));
    const q = section("quotes");
    const sel = q.querySelector('select[aria-label="시장"]') as HTMLSelectElement;
    const setter = Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype, "value")!.set!;
    act(() => {
      setter.call(sel, "US");
      sel.dispatchEvent(new Event("change", { bubbles: true }));
    });
    type(q.querySelector('input[aria-label="종목 검색"]') as HTMLInputElement, "AAPL");
    act(() => button(q, "검색").click());
    await flush();
    act(() => button(q, "Apple AAPL").click());
    await flush();
    expect(callsTo("/charts")[0].search).toBe("?ticker=AAPL&market=US&days=120&exchange=NAS");
    expect(callsTo("/quotes/book")).toHaveLength(0);
  });

  test("테마·지표·모델: 정렬(aria-sort)·행 클릭 이력, 활성 모델·잡 버튼", async () => {
    const t = await mount(routes(), "themes");
    expect(section("themes").textContent).toContain("반도체");
    expect(section("themes").textContent).toContain("거래량 급증");
    t.unmount();
    await mount(routes(), "features");
    const f = section("features");
    const th = f.querySelector('th[data-key="roe"]') as HTMLElement;
    expect(th.getAttribute("aria-sort")).toBe("none");
    act(() => (th.querySelector("button") as HTMLButtonElement).click());
    expect(th.getAttribute("aria-sort")).toBe("descending");
    const firstRow = f.querySelector("tbody tr") as HTMLElement;
    expect(firstRow.textContent).toContain("000660");
    act(() => firstRow.click());
    await flush();
    const hist = callsTo("/features/history")[0];
    expect(hist.search).toBe("?market=KR&ticker=000660&days=120");
    expect(f.textContent).toContain("활성 20261101_120000");
    expect(f.textContent).toContain("판정 보조 신호로만 쓴다");
    for (const label of ["지표 스냅샷", "파생 지표 계산", "지표 백필"]) expect(button(f, label)).toBeDefined();
  });

  test("지표·모델은 SSE features 키로 다시 읽고 market_views 로는 읽지 않는다", async () => {
    const cbs: EventsCallback[] = [];
    setEventsClient({ ...stub, subscribe: (cb) => (cbs.push(cb), () => {}) });
    await mount(routes(), "features");
    act(() => (section("features").querySelector("tbody tr") as HTMLElement).click());
    await flush();
    const counts = () => ["/features/latest", "/ml/models", "/features/history"].map((p) => callsTo(p).length);
    const before = counts();
    act(() => cbs.forEach((cb) => cb(["market_views"], {} as EventSnapshot)));
    await flush();
    expect(counts()).toEqual(before);
    act(() => cbs.forEach((cb) => cb(["features"], {} as EventSnapshot)));
    await flush();
    expect(counts()).toEqual(before.map((n) => n + 1));
  });

  test("한 절의 오류는 그 절에 보이고 다른 절은 그대로", async () => {
    await mount(
      routes({
        "GET /api/news": () => new Response(JSON.stringify({ detail: "뉴스 저장소 오류" }), { status: 500, headers: { "Content-Type": "application/json" } }),
      }),
      "news",
    );
    expect(section("news").textContent).toContain("불러오지 못했습니다");
    expect(section("news").textContent).toContain("뉴스 저장소 오류");
    // 같은 절 안의 공시 표는 그대로
    expect(section("news").textContent).toContain("주요사항보고서(유상증자결정)");
  });
});
