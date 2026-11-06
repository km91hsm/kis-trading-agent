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
  fixtureMinute,
  fixtureModelDetail,
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
    "GET /api/ml/models/20261101_120000": fixtureModelDetail(),
    "GET /api/charts/minute": fixtureMinute(),
    "GET /api/settings/watchlist": fixtureWatchlist(),
    "PATCH /api/settings/watchlist": (_u: URL, init?: RequestInit) => JSON.parse(String(init?.body)) as unknown,
    "GET /api/charts": fixtureCharts(),
    "GET /api/quotes/book": fixtureBook(),
    "GET /api/quotes": fixtureQuotes(),
    ...over,
  };
}

type Page = Parameters<typeof Data>[0]["page"];
async function mount(r: Routes, page: Page = "quotes", path = "/data") {
  ff = installFakeFetch(r);
  const out = render(
    <MemoryRouter initialEntries={[path]}>
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
async function confirmDialog() {
  await act(async () => button(document.querySelector('[role="dialog"]') as HTMLElement, "확인").click());
  await flush();
}
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
    // 뉴스·공시가 각자 이름을 읽는다 — 뉴스 쪽 호출은 두 종목
    const nm = callsTo("/names").find((u) => u.searchParams.get("tickers") === "005930,000660")!;
    expect(nm.searchParams.get("market")).toBe("KR");
    expect(callsTo("/decisions/spot/news-refs")[0].searchParams.get("date")).toMatch(/^\d{4}-\d{2}-\d{2}$/);
    expect(news.textContent).toContain("삼성전자 HBM 공급 확대");
    expect(news.textContent).toContain("삼성전자");
    expect(news.textContent).toContain("촉매");
    // 출처 한글·시각 fmtWhen(다른 날이면 MM.DD HH:MM)
    expect(news.textContent).toContain("네이버");
    expect(news.textContent).not.toContain("naver");
    expect(news.textContent).toContain("11.05 09:30");
    expect(Array.from(news.querySelectorAll("thead")[0].querySelectorAll("th")).map((e) => e.textContent)).toEqual(["시각", "제목", "종목", "꼬리표", "출처"]);
    const link = Array.from(news.querySelectorAll("a")).find((a) => a.textContent === "판정에 쓰임") as HTMLAnchorElement;
    expect(link.getAttribute("href")).toBe("/decisions/spot/7");
    expect(news.textContent).toContain("데몬이 주기적으로 수집");
    expect(button(news, "지금 수집")).toBeDefined();
    // 공시 — 중요도 칩 한글, 날짜만, 출처 한글
    expect(news.textContent).toContain("주요사항보고서(유상증자결정)");
    expect(news.querySelector('.chip[data-tone="fail"]')?.textContent).toBe("긴급");
    expect(news.textContent).toContain("전자공시(DART)");
    const ev = news.querySelectorAll("table")[1];
    expect(Array.from(ev.querySelectorAll("thead th")).map((e) => e.textContent)).toEqual(["날짜", "중요도", "제목", "종목", "출처"]);
    expect(ev.querySelector("tbody td")!.textContent).toBe("2026-11-05");
    // 검색 안내 통일
    expect(section("news").querySelector("select")).toBeNull();
  });

  test("꼬리표가 전 행 같으면(전부 「촉매」) 열을 빼고 머리줄에 한 번만; 공시 중요도가 전부 없으면 중요도 열도 뺀다; 공시 종목은 코드+이름", async () => {
    const n = fixtureNews();
    const same = Object.fromEntries((n.items as { id: number }[]).map((i) => [String(i.id), { catalyst: true, themes: [] }]));
    const e = fixtureEvents();
    await mount(
      routes({
        "GET /api/news": { ...n, items: (n.items as { id: number }[]).map((i) => ({ ...i, tickers: ["005930"] })) },
        "GET /api/news/labels": same,
        "GET /api/decisions/spot/news-refs": {},
        "GET /api/news/events": { ...e, disclosures: (e.disclosures as { severity: string | null }[]).map((d) => ({ ...d, severity: null })) },
      }),
      "news",
      "/data/news",
    );
    const news = section("news");
    const heads = (t: Element) => Array.from(t.querySelectorAll("thead th")).map((x) => x.textContent);
    expect(heads(news.querySelectorAll("table")[0])).toEqual(["시각", "제목", "종목", "출처"]);
    expect(news.querySelector("[data-news-chips-note]")!.textContent).toContain("촉매");
    const ev = news.querySelectorAll("table")[1];
    expect(heads(ev)).toEqual(["날짜", "제목", "종목", "출처"]);
    expect(ev.textContent).toContain("005930 삼성전자");
  });

  test("뉴스 종목 칩을 누르면 ?ticker= 로 다시 읽고, 필터 줄에 「차트 보기」 링크와 해제 버튼", async () => {
    await mount(routes(), "news", "/data/news");
    const news = section("news");
    const chip = Array.from(news.querySelectorAll("button.chip")).find((b) => b.textContent === "삼성전자") as HTMLButtonElement;
    expect(chip).toBeDefined();
    await act(async () => chip.click());
    await flush();
    await flush();
    const last = callsTo("/news").at(-1)!;
    expect(last.searchParams.get("ticker")).toBe("005930");
    const bar = news.querySelector("[data-news-filter]") as HTMLElement;
    expect(bar.textContent).toContain("005930 삼성전자");
    expect((bar.querySelector("a") as HTMLAnchorElement).getAttribute("href")).toBe("/data?ticker=005930");
    await act(async () => button(bar, "해제").click());
    await flush();
    expect(news.querySelector("[data-news-filter]")).toBeNull();
    expect(news.querySelectorAll("table")[0].querySelectorAll("tbody tr")).toHaveLength(2);
  });

  test("뉴스 종목 열은 종목이 하나도 없으면 빼고, 중요도 없는 공시는 칩 대신 「—」", async () => {
    const n = fixtureNews();
    await mount(
      routes({
        "GET /api/news": { ...n, items: (n.items as { tickers: string[] }[]).map((i) => ({ ...i, tickers: [] })) },
        "GET /api/news/events": { ...fixtureEvents(), disclosures: [{ id: 202, source: "dart", title: "기타 공시", url: null, published_at: null, received_at: "2026-11-05T01:01:00+00:00", severity: null, tickers: [] }] },
      }),
      "news",
    );
    const news = section("news");
    const heads = Array.from(news.querySelectorAll("table")[0].querySelectorAll("thead th")).map((e) => e.textContent);
    expect(heads).not.toContain("종목");
    const ev = news.querySelectorAll("table")[1];
    expect(ev.querySelector(".chip")).toBeNull();
  });

  test("뉴스·공시는 30행 페이지(?page / ?epage)", async () => {
    const n = fixtureNews();
    const base = n.items[0] as Record<string, unknown>;
    const many = Array.from({ length: 31 }, (_, i) => ({ ...base, id: 1000 + i, title: `기사 ${i}` }));
    const e = fixtureEvents();
    const ebase = e.disclosures[0] as Record<string, unknown>;
    const manyEv = Array.from({ length: 31 }, (_, i) => ({ ...ebase, id: 2000 + i, title: `공시 ${i}` }));
    await mount(routes({ "GET /api/news": { ...n, items: many }, "GET /api/news/events": { ...e, disclosures: manyEv } }), "news");
    const tables = section("news").querySelectorAll("table");
    expect(tables[0].querySelectorAll("tbody tr")).toHaveLength(30);
    expect(tables[1].querySelectorAll("tbody tr")).toHaveLength(30);
    expect(section("news").querySelectorAll(".pager")).toHaveLength(2);
  });

  test("시장은 화면 머리의 세그먼트 하나(KR | US)로 고르고 ?market= 에 남는다 — 절 안에 select 없음", async () => {
    await mount(routes(), "news");
    const seg = document.querySelector('[data-market-segment]') as HTMLElement;
    const us = Array.from(seg.querySelectorAll("button")).find((b) => b.textContent === "US")!;
    expect(us.getAttribute("aria-pressed")).toBe("false");
    act(() => us.click());
    await flush();
    await flush();
    expect(us.getAttribute("aria-pressed")).toBe("true");
    expect(callsTo("/news").at(-1)!.searchParams.get("market")).toBe("US");
    expect(callsTo("/news/events").at(-1)!.searchParams.get("market")).toBe("US");
    expect(document.querySelectorAll('select[aria-label="시장"]')).toHaveLength(0);
  });

  test("?market=US 는 페이지 탭 링크에도 붙는다(KR 기본이면 안 붙음)", async () => {
    ff = installFakeFetch(routes());
    const { unmount } = render(
      <MemoryRouter initialEntries={["/data/news?market=US"]}>
        <Data page="news" />
      </MemoryRouter>,
    );
    await flush();
    expect(Array.from(document.querySelectorAll(".page-tabs a")).map((e) => e.getAttribute("href"))).toEqual([
      "/data?market=US",
      "/data/news?market=US",
      "/data/themes?market=US",
      "/data/features?market=US",
    ]);
    unmount();
    render(
      <MemoryRouter initialEntries={["/data/news"]}>
        <Data page="news" />
      </MemoryRouter>,
    );
    await flush();
    expect(Array.from(document.querySelectorAll(".page-tabs a")).map((e) => e.getAttribute("href"))).toEqual(["/data", "/data/news", "/data/themes", "/data/features"]);
  });

  test("?market=US 로 들어오면 지표·테마도 US 로 읽는다", async () => {
    ff = installFakeFetch(routes());
    render(
      <MemoryRouter initialEntries={["/data/features?market=US"]}>
        <Data page="features" />
      </MemoryRouter>,
    );
    await flush();
    await flush();
    expect(callsTo("/features/latest")[0].searchParams.get("market")).toBe("US");
  });

  test("「지금 수집」: POST /jobs {kind: news_collect, params: {market}}", async () => {
    tokenStore.set("tok");
    await mount(routes({ "POST /api/jobs": { id: 9, kind: "news_collect", params: { market: "KR" }, status: "queued", label: "뉴스 수집" } }), "news");
    act(() => button(section("news"), "지금 수집").click());
    await act(async () => button(document.querySelector('[role="dialog"]') as HTMLElement, "확인").click());
    await flush();
    expect(bodiesTo("/jobs", "POST")).toEqual([{ kind: "news_collect", params: { market: "KR" } }]);
  });

  test("수동 입력은 접힌 details(요약 「수동 입력」) 안, 저장은 확인 뒤 POST /news/manual {market, text, url}, 최근 입력은 표(시각·제목)", async () => {
    tokenStore.set("tok");
    await mount(routes({ "GET /api/news/manual": { market: "KR", items: [{ received_at: "2026-11-05T02:00:00+00:00", title: "지난 수동 기사", url: "https://m.example/0", note: "" }] } }), "news");
    const news = section("news");
    const details = news.querySelector("details.data-manual-details") as HTMLDetailsElement;
    expect(details.open).toBe(false);
    expect(details.querySelector("summary")!.textContent).toContain("수동 입력");
    type(news.querySelector('textarea[aria-label="기사 붙여 넣기"]') as HTMLTextAreaElement, "수동 기사\n메모");
    type(news.querySelector('input[aria-label="URL"]') as HTMLInputElement, "https://m.example/1");
    await act(async () => button(news, "저장").click());
    expect(bodiesTo("/news/manual", "POST")).toEqual([]);
    expect(document.querySelector('[role="dialog"]')!.textContent).toContain("수동 기사");
    await confirmDialog();
    expect(bodiesTo("/news/manual", "POST")).toEqual([{ market: "KR", text: "수동 기사\n메모", url: "https://m.example/1" }]);
    expect(news.textContent).toContain("저장했습니다");
    const recent = details.querySelector("table")!;
    expect(Array.from(recent.querySelectorAll("thead th")).map((e) => e.textContent)).toEqual(["시각", "제목"]);
    expect(recent.textContent).toContain("지난 수동 기사");
  });

  test("감시목록 PATCH 는 토큰이 필요하다 — 없으면 오류 줄, 있으면 {tickers}", async () => {
    await mount(routes(), "themes");
    const wl = section("watchlist");
    expect(wl.textContent).toContain("005930");
    type(wl.querySelector('input[aria-label="종목 추가"]') as HTMLInputElement, "AAPL");
    act(() => button(wl, "추가").click());
    // 추가 즉시 임시 칩(저장 전 표시)
    const draft = Array.from(wl.querySelectorAll(".chip")).find((c) => c.textContent?.startsWith("AAPL")) as HTMLElement;
    expect(draft.getAttribute("data-draft")).toBe("true");
    await act(async () => button(wl, "저장").click());
    await confirmDialog();
    expect(callsTo("/settings/watchlist", "PATCH")).toHaveLength(0);
    expect(wl.querySelector('[role="alert"]')?.textContent).toContain("쓰기 토큰이 필요합니다");

    tokenStore.set("tok");
    act(() => (wl.querySelector('button[aria-label="000660 빼기"]') as HTMLButtonElement).click());
    await act(async () => button(wl, "저장").click());
    expect(document.querySelector('[role="dialog"]')!.textContent).toContain("2종목");
    await confirmDialog();
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
    expect(section("quotes").textContent).toContain("71,600");
    expect(section("quotes").textContent).not.toContain("71600");
    expect((section("quotes").querySelector('input[aria-label="종목 검색"]') as HTMLInputElement).placeholder).toBe("종목명 또는 코드로 검색");
  });

  test("차트: 「1분」 토글 → /charts/minute?ticker=&market=&count=120, epoch 캔들·체결 표식, 비면 「오늘 분봉 없음」", async () => {
    await mount(routes());
    await pickSamsung();
    const q = section("quotes");
    const seg = q.querySelector("[data-chart-interval]") as HTMLElement;
    expect(Array.from(seg.querySelectorAll("button")).map((b) => [b.textContent, b.getAttribute("aria-pressed")])).toEqual([
      ["일봉", "true"],
      ["1분", "false"],
    ]);
    expect(callsTo("/charts/minute")).toHaveLength(0);
    await act(async () => button(seg, "1분").click());
    await flush();
    expect(callsTo("/charts/minute")[0].search).toBe("?ticker=005930&market=KR&count=120");
    expect(m.series.setData).toHaveBeenLastCalledWith([
      { time: 1762302600, open: 70000, high: 70100, low: 69900, close: 70050 },
      { time: 1762302660, open: 70050, high: 70200, low: 70000, close: 70150 },
    ]);
    expect(m.chart.applyOptions).toHaveBeenCalledWith(expect.objectContaining({ timeScale: expect.objectContaining({ timeVisible: true }) }));
    // 「일봉」 으로 돌아가면 일봉은 다시 부르지 않는다(캐시)
    await act(async () => button(seg, "일봉").click());
    await flush();
    expect(callsTo("/charts")).toHaveLength(1);
  });

  test("장 밖이라 분봉이 비면 「오늘 분봉 없음」", async () => {
    await mount(routes({ "GET /api/charts/minute": fixtureMinute({ session_date: null, candles: [], markers: [] }) }));
    await pickSamsung();
    await act(async () => button(section("quotes").querySelector("[data-chart-interval]") as HTMLElement, "1분").click());
    await flush();
    expect(section("quotes").textContent).toContain("오늘 분봉 없음");
  });

  test("?ticker= 로 들어오면 검색 없이 그 종목 차트를 바로 연다(이름은 /names/search 로)", async () => {
    await mount(routes(), "quotes", "/data?ticker=005930");
    expect(callsTo("/names/search")[0].searchParams.get("q")).toBe("005930");
    expect(callsTo("/charts")[0].searchParams.get("ticker")).toBe("005930");
    expect(section("quotes").querySelector("h3")!.textContent).toContain("삼성전자");
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
    const seg = document.querySelector("[data-market-segment]") as HTMLElement;
    act(() => Array.from(seg.querySelectorAll("button")).find((b) => b.textContent === "US")!.click());
    await flush();
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
    const tt = section("themes").querySelector("table")!;
    expect(Array.from(tt.querySelectorAll("thead th")).map((e) => e.textContent)).toEqual(["종목", "근거"]);
    expect(tt.textContent).toContain("005930 삼성전자");
    t.unmount();
    await mount(routes(), "features");
    const f = section("features");
    const th = f.querySelector('th[data-key="roe"]') as HTMLElement;
    expect(th.textContent).toBe("ROE");
    expect((f.querySelector('th[data-key="close"]') as HTMLElement).textContent).toBe("종가");
    expect((f.querySelector('th[data-key="flow_foreign_20d"]') as HTMLElement).textContent).toBe("외국인 20일 순매수");
    expect(th.getAttribute("aria-sort")).toBe("none");
    act(() => (th.querySelector("button") as HTMLButtonElement).click());
    expect(th.getAttribute("aria-sort")).toBe("descending");
    const firstRow = f.querySelector("tbody tr") as HTMLElement;
    expect(firstRow.textContent).toContain("000660");
    // 종목은 코드+이름(/names, 이 쪽 30행만), 숫자는 규칙대로(종가 천 단위·비율 %·순매수 천 단위)
    expect(callsTo("/names")[0].searchParams.get("tickers")).toBe("005930,000660,035420");
    expect(f.textContent).toContain("005930 삼성전자");
    const cells = (tr: Element) => Array.from(tr.querySelectorAll("td")).map((c) => c.textContent);
    const samsung = Array.from(f.querySelectorAll("tbody tr")).find((tr) => tr.textContent?.includes("005930"))!;
    expect(cells(samsung)).toEqual(expect.arrayContaining(["71,500", "3.12%", "80.00%", "10.00%", "0.1", "3"]));
    act(() => firstRow.click());
    await flush();
    const hist = callsTo("/features/history")[0];
    expect(hist.search).toBe("?market=KR&ticker=000660&days=120");
    const histTable = f.querySelector(".data-history table")!;
    expect(Array.from(histTable.querySelectorAll("thead th")).map((e) => e.textContent)).toEqual(["날짜", "종가", "10일 수익률", "52주 고점 위치", "외국인 20일 순매수", "뉴스 수"]);
    expect(f.textContent).toContain("활성 20261101_120000");
    expect(f.textContent).toContain("판정 보조 신호로만 쓴다");
    for (const label of ["지표 스냅샷", "파생 지표 계산", "지표 백필"]) expect(button(f, label)).toBeDefined();
  });

  test("모델 행을 펼치면 /ml/models/{version} — 학습 기간·행·피처 수·지표 Stats + 중요도 표", async () => {
    await mount(routes(), "features");
    const f = section("features");
    const models = f.querySelectorAll("table")[1];
    expect(callsTo("/ml/models/20261101_120000")).toHaveLength(0);
    await act(async () => (models.querySelector('button[aria-label="펼치기"]') as HTMLButtonElement).click());
    await flush();
    await flush();
    expect(callsTo("/ml/models/20261101_120000")).toHaveLength(1);
    const ex = models.querySelector(".table-expand") as HTMLElement;
    const dts = Array.from(ex.querySelectorAll("dl.stats dt")).map((e) => e.textContent);
    expect(dts).toEqual(expect.arrayContaining(["학습 기간", "행 수", "피처 수", "표본 수(n)", "정확도", "기준선", "알파"]));
    expect(ex.textContent).toContain("2023-01-02 ~ 2026-10-31");
    expect(ex.textContent).toContain("5,000");
    const imp = ex.querySelector("table")!;
    expect(Array.from(imp.querySelectorAll("thead th")).map((e) => e.textContent)).toEqual(["피처", "중요도"]);
    // 겹친 표라 "tbody tr" 은 바깥 tbody 의 자손인 안쪽 thead 행도 맞는다 — 안쪽 tbody 를 먼저 잡는다
    expect(imp.querySelector("tbody")!.querySelector("tr")!.textContent).toContain("ROE");
  });

  test("지표 표는 30행 페이지(?page)", async () => {
    const l = fixtureFeaturesLatest();
    const base = l.rows[0] as Record<string, unknown>;
    const many = Array.from({ length: 31 }, (_, i) => ({ ...base, ticker: String(100000 + i) }));
    await mount(routes({ "GET /api/features/latest": { ...l, rows: many } }), "features");
    const f = section("features");
    expect(f.querySelectorAll("table.data-feature-table tbody tr")).toHaveLength(30);
    expect(f.querySelector(".pager")).not.toBeNull();
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
