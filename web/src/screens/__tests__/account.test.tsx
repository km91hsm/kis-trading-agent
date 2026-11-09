import { act } from "react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

vi.mock("lightweight-charts", () => {
  const series = { setData: vi.fn(), createPriceLine: vi.fn(() => ({})), removePriceLine: vi.fn() };
  const chart = { addSeries: vi.fn(() => series), applyOptions: vi.fn(), remove: vi.fn(), timeScale: vi.fn(() => ({ fitContent: vi.fn() })) };
  return {
    createChart: vi.fn(() => chart),
    createSeriesMarkers: vi.fn(() => ({ setMarkers: vi.fn(), detach: vi.fn() })),
    LineSeries: { type: "Line" },
  };
});

import { Account } from "../accounts/Account.tsx";
import { tokenStore } from "../../lib/api";
import { clearResourceCache } from "../../lib/cache";
import { setEventsClient, type EventsClient } from "../../lib/sse";
import { installFakeFetch, type FakeFetch } from "../../test/fakeFetch";
import { flush, render } from "../../test/render";
import {
  fixtureAccounts,
  fixtureFoPositions,
  fixtureFoReport,
  fixturePerformance,
  fixturePositions,
  fixtureRisk,
  fixtureTaxLots,
} from "../../test/fixtures/accounts";
import { fixtureSpotOrder } from "../../test/fixtures/today";

let ff: FakeFetch | null = null;
const stub: EventsClient = { subscribe: () => () => {}, status: () => ({ connected: true, since: 0 }), close() {} };

beforeEach(() => {
  clearResourceCache();
  tokenStore.set("tok");
  setEventsClient(stub);
});
afterEach(() => {
  ff?.restore();
  ff = null;
});

type Routes = Parameters<typeof installFakeFetch>[0];
function routes(over: Routes = {}): Routes {
  return {
    "GET /api/accounts": fixtureAccounts(),
    "GET /api/accounts/paper-1/positions": fixturePositions(),
    "GET /api/accounts/real-1/positions": fixturePositions(),
    "GET /api/accounts/paper-fo/positions": fixtureFoPositions(),
    "GET /api/accounts/paper-1/tax-lots": fixtureTaxLots(),
    "GET /api/accounts/real-1/tax-lots": fixtureTaxLots(),
    "GET /api/orders": { items: [], partial: false },
    "GET /api/positions/risk": fixtureRisk(),
    "GET /api/analytics/performance": fixturePerformance(),
    "GET /api/analytics/fo/report": fixtureFoReport(),
    "GET /api/quotes": { session: "closed", at: new Date().toISOString(), quotes: {}, source: "rest", next_poll_ms: 60000 },
    "GET /api/quotes/fo": { session: "closed", at: new Date().toISOString(), quotes: {}, kospi200: null, next_poll_ms: 60000 },
    ...over,
  };
}

async function mount(r: Routes, id = "paper-1") {
  ff = installFakeFetch(r);
  const out = render(
    <MemoryRouter initialEntries={[`/accounts/${id}`]}>
      <Routes>
        <Route path="/accounts/:id" element={<Account />} />
      </Routes>
    </MemoryRouter>,
  );
  await flush();
  await flush();
  return out;
}
const section = (id: string) => document.getElementById(id) as HTMLElement | null;
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
function choose(select: HTMLSelectElement, value: string) {
  const setter = Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype, "value")!.set!;
  act(() => {
    setter.call(select, value);
    select.dispatchEvent(new Event("change", { bubbles: true }));
  });
}
const patches = () => ff!.calls.filter((c) => c.init?.method === "PATCH");
const bodyOf = (c: { init?: RequestInit }) => JSON.parse(String(c.init!.body));
const dialog = () => document.querySelector('[role="dialog"]') as HTMLElement;
const head = () => document.querySelector(".account-head") as HTMLElement;

describe("계좌별 화면", () => {
  test("paused 면 모드 옆 「(전체 일시정지로 새 매수 멈춤)」", async () => {
    await mount(routes({ "GET /api/accounts": fixtureAccounts().map((a) => ({ ...a, paused: true })) }), "paper-1");
    expect(document.body.textContent).toContain("(전체 일시정지로 새 매수 멈춤)");
  });

  test("paused 아니면 「(전체 일시정지로 새 매수 멈춤)」 없음", async () => {
    await mount(routes(), "paper-1");
    expect(document.body.textContent).not.toContain("전체 일시정지로 새 매수 멈춤");
  });

  test("실전 계좌 auto 전환은 계좌 id 재입력 뒤 PATCH /accounts/real-1 {mode:'auto', confirm:'real-1'}", async () => {
    let gets = 0;
    const accounts = fixtureAccounts().map((a) => (a.id === "real-1" ? { ...a, mode: "approve" } : a));
    await mount(
      routes({
        "GET /api/accounts": () => {
          gets += 1;
          return accounts;
        },
        "PATCH /api/accounts/real-1": (_u: URL, init?: RequestInit) => ({ account_id: "real-1", mode: JSON.parse(String(init!.body)).mode }),
      }),
      "real-1",
    );
    const mode = head();
    const select = mode.querySelector("select") as HTMLSelectElement;
    expect(Array.from(select.options).map((o) => o.value)).toEqual(["approve", "off", "auto"]);
    choose(select, "auto");
    act(() => button(mode, "적용").click());
    expect(button(dialog(), "확인").disabled).toBe(true);
    type(dialog().querySelector("input") as HTMLInputElement, "real-1");
    act(() => button(dialog(), "확인").click());
    await flush();
    expect(patches()).toHaveLength(1);
    expect(patches()[0].url).toBe("/api/accounts/real-1");
    expect(bodyOf(patches()[0])).toEqual({ mode: "auto", confirm: "real-1" });
    expect(gets).toBe(2);
    expect(dialog()).toBeNull();
  });

  test("모의 계좌 모드 전환은 confirm 없이 PATCH; 실패는 한 줄 오류", async () => {
    let fail = false;
    await mount(
      routes({
        "PATCH /api/accounts/paper-1": () =>
          fail
            ? new Response(JSON.stringify({ detail: "모드 값 오류" }), { status: 400, headers: { "Content-Type": "application/json" } })
            : { account_id: "paper-1", mode: "off" },
      }),
    );
    const mode = head();
    const select = mode.querySelector("select") as HTMLSelectElement;
    expect(select.value).toBe("approve");
    choose(select, "off");
    act(() => button(mode, "적용").click());
    expect(dialog().querySelector("input")).toBeNull();
    act(() => button(dialog(), "확인").click());
    await flush();
    expect(bodyOf(patches()[0])).toEqual({ mode: "off" });

    fail = true;
    choose(select, "auto");
    act(() => button(mode, "적용").click());
    act(() => button(dialog(), "확인").click());
    await flush();
    expect(mode.querySelector('[role="alert"]')!.textContent).toContain("모드 값 오류");
  });

  test("시세 응답이 오면 현재가·평가손익이 live 로 바뀐다(fakeFetch /quotes)", async () => {
    const at = new Date().toISOString();
    const markets: string[] = [];
    await mount(
      routes({
        "GET /api/quotes": (u: URL) => {
          markets.push(`${u.searchParams.get("market")}:${u.searchParams.get("tickers")}`);
          return {
            session: "open",
            at,
            quotes: u.searchParams.get("market") === "KR" ? { "005930": { price: "72000", change_pct: 0.01, at, stale: false, source: "ws" } } : {},
            source: "ws",
            next_poll_ms: 60000,
          };
        },
      }),
    );
    expect(markets.sort()).toEqual(["KR:005930,000660", "US:AAPL"]);
    const pos = section("positions")!;
    expect(pos.textContent).toContain("72,000");
    expect(pos.textContent).toContain("+20,000");
    expect(pos.textContent).toContain("실시간");
    expect(pos.textContent).toContain("65,100 · 추적");
    expect(pos.textContent).toContain("80,000 · 결정");
  });

  test("시세가 없으면 서버 현재가로 평가·합계(칩 없음)", async () => {
    await mount(routes());
    const pos = section("positions")!;
    expect(pos.textContent).toContain("71,000");
    expect(pos.textContent).toContain("+10,000");
    expect(pos.textContent).toContain("210.00");
    expect(pos.textContent).toContain("평가금액");
    expect(pos.textContent).toContain("매수금액");
    expect(pos.textContent).toContain("매수 합계(KRW) 700,000 · 평가 합계(KRW) 710,000 · 손익 +10,000");
    expect(pos.textContent).toContain("평가 합계(USD) 420.00 · 손익 +19.00");
    expect(pos.textContent).not.toContain("실시간");
    expect(pos.textContent).not.toContain("지연");
  });

  test("낡은 시세(isStale) 행은 가격을 보이고 「지연」 칩", async () => {
    const at = new Date(Date.now() - 120_000).toISOString();
    await mount(
      routes({
        "GET /api/quotes": (u: URL) => ({
          session: "closed",
          at,
          quotes: u.searchParams.get("market") === "KR" ? { "005930": { price: "72000", change_pct: 0.01, at, stale: false, source: "ws" } } : {},
          source: "ws",
          next_poll_ms: 60000,
        }),
      }),
    );
    const pos = section("positions")!;
    expect(pos.textContent).toContain("71,000");
    expect(pos.textContent).not.toContain("72,000");
    expect(pos.querySelector('.chip[data-tone="muted"]')).not.toBeNull();
    expect(pos.textContent).toContain("지연");
    expect(pos.textContent).not.toContain("실시간");
  });

  test("partial 이면 표 위 칩", async () => {
    await mount(routes({ "GET /api/accounts/paper-1/positions": fixturePositions({ partial: true }) }));
    expect(section("positions")!.textContent).toContain("일부 레인 응답 없음");
  });

  test("단타 행은 「단타」 절로 빠지고, 현물 표·리스크·NAV·lot 펼침을 그린다", async () => {
    await mount(routes());
    const pos = section("positions")!;
    expect(pos.textContent).toContain("005930");
    expect(pos.textContent).toContain("AAPL");
    expect(pos.textContent).not.toContain("000660");
    const scalp = section("scalp-positions")!;
    expect(scalp.hidden).toBe(false);
    expect(scalp.textContent).toContain("000660");
    expect(scalp.textContent).toContain("단타 규칙");
    expect(section("fo-positions")!.hidden).toBe(true);
    expect(section("nav-chart")).not.toBeNull();
    const risk = section("risk")!;
    expect(risk.textContent).toContain("2.13%");
    expect(risk.textContent).toContain("3.42%");
    expect(risk.querySelectorAll(".risk-bar")).toHaveLength(3);
    const lots = ff!.calls.find((c) => c.url.includes("/tax-lots"))!;
    expect(lots.url).toContain("market=KR");
    act(() => (pos.querySelector(".row-toggle") as HTMLButtonElement).click());
    expect(pos.textContent).toContain("69,000");
    expect(pos.textContent).toContain("71,500");
    expect(pos.querySelector(".lot-list")!.textContent).toContain("6주");
    const orders = ff!.calls.find((c) => c.url.startsWith("/api/orders"))!;
    expect(orders.url).toContain("account_id=paper-1");
  });

  test("리스크: VaR·CVaR·베타는 Stats, 한도는 표(항목·현재·한도·판정) — 값 없는 줄은 막대 없이 「자료 없음」, 현금은 「최소」, 기여 상위 3 은 코드+이름 막대; 자료 부족은 Empty 한 줄", async () => {
    const limits = { max_position_pct: 0.25, max_positions: 5, max_sector_pct: 0.4, min_cash_pct: 0.1, daily_loss_stop_pct: -0.05 };
    const usage = { position_pct_max: 0.3, positions: 3, sector_pct_max: 0.2, cash_pct: 0.05, daily_loss_pct: null };
    const first = await mount(routes({ "GET /api/positions/risk": fixtureRisk({ limits, usage }) }));
    const risk = section("risk")!;
    expect(risk.querySelector('dl.stats [data-stat="var95"] dd')?.textContent).toBe("2.13%");
    expect(risk.querySelector('dl.stats [data-stat="beta"] dd')?.textContent).toBe("1.05");
    expect(risk.textContent).toContain("한도 대비");
    const table = risk.querySelector(".risk-limits table")!;
    expect(Array.from(table.querySelectorAll("thead th")).map((e) => e.textContent)).toEqual(["항목", "현재", "한도", "판정"]);
    const rows = Array.from(table.querySelectorAll("tbody tr")).map((r) => r.textContent ?? "");
    expect(rows).toHaveLength(5);
    expect(rows[0]).toContain("30.00%");
    expect(rows[0]).toContain("최대 25.00%");
    expect(rows[0]).toContain("초과");
    expect(rows[1]).toContain("3");
    expect(rows[1]).toContain("최대 5");
    expect(rows[1]).toContain("정상");
    expect(rows[3]).toContain("최소 10.00%");
    expect(rows[3]).toContain("미달");
    expect(rows[4]).toContain("손실 -5.00%");
    expect(rows[4]).toContain("자료 없음");
    expect(table.querySelectorAll('.chip[data-tone="fail"]')).toHaveLength(2);
    expect(risk.querySelectorAll(".risk-bar")).toHaveLength(3); // 기여 상위 3 만 막대
    expect(risk.querySelector(".risk-contrib li")!.textContent).toContain("005930 삼성전자");
    first.unmount();
    ff!.restore();
    clearResourceCache();
    await mount(routes({ "GET /api/positions/risk": fixtureRisk({ insufficient: "종가 부족(3일)", limits, usage }) }));
    const thin = section("risk")!;
    expect(thin.querySelector(".empty")!.textContent).toContain("종가 부족(3일)");
    expect(thin.querySelector(".risk-insufficient")).toBeNull();
    expect(thin.querySelectorAll(".risk-limits tbody tr")).toHaveLength(5);
  });

  test("머리: h1 「계좌 id」, Stats(NAV·현금 + 갱신 시각), 모드 전환은 머리 카드 안(#mode 절 없음); 절 순서 주문 → 보유 → 단타 → 리스크 → NAV", async () => {
    const { container } = await mount(routes());
    expect(container.querySelector("h1.screen-title")?.textContent).toBe("계좌 paper-1");
    const h = head();
    expect(h.querySelector('dl.stats [data-stat="nav"] dd')?.textContent).toBe("10,000,000");
    expect(h.querySelector('dl.stats [data-stat="cash"] dd')?.textContent).toBe("5,000,000");
    expect(h.querySelector('dl.stats [data-stat="cash"] .stat-sub')?.textContent).toMatch(/전$/);
    expect(h.textContent).not.toContain("현금 현금");
    expect(h.querySelector("select")).not.toBeNull();
    expect(section("mode")).toBeNull();
    expect(Array.from(container.querySelectorAll("section.screen-section[id]")).map((e) => e.id)).toEqual([
      "orders", "positions", "fo-positions", "scalp-positions", "risk",
      "tax-lots", "nav-chart",
    ]);
  });

  test("주문 표: 상태는 한글 칩(실패는 강조), 수량은 천 단위", async () => {
    await mount(routes({ "GET /api/orders": { items: [fixtureSpotOrder({ status: "failed", qty: 18018, filled_qty: 0 }), fixtureSpotOrder({ id: 22, status: "filled", qty: 3, filled_qty: 3 })], partial: false } }));
    const orders = section("orders")!;
    const rows = Array.from(orders.querySelectorAll("tbody tr"));
    expect(rows[0].querySelector('.chip[data-tone="fail"]')?.textContent).toBe("실패");
    expect(rows[0].textContent).toContain("18,018");
    expect(rows[1].querySelector('.chip[data-tone="ok"]')?.textContent).toBe("체결");
    expect(orders.textContent).not.toContain("failed");
  });

  test("주문 표 「종목」 칸은 코드+이름", async () => {
    await mount(routes({ "GET /api/orders": { items: [fixtureSpotOrder({ name: "삼성전자" })], partial: false } }));
    const orders = section("orders")!;
    expect(orders.textContent).toContain("005930 삼성전자");
  });

  test("주문 표 CSV 링크는 주문이 있을 때만(/orders?date&account_id&format=csv)", async () => {
    await mount(routes({ "GET /api/orders": { items: [fixtureSpotOrder({ account_id: "paper-1" })], partial: false } }));
    const a = section("orders")!.querySelector("a.table-csv") as HTMLAnchorElement;
    expect(a).not.toBeNull();
    expect(a.getAttribute("href")).toMatch(/^\/api\/orders\?date=\d{4}-\d{2}-\d{2}&account_id=paper-1&format=csv$/);
  });

  test("주문이 없으면 CSV 링크도 없다", async () => {
    await mount(routes());
    expect(section("orders")!.querySelector("a.table-csv")).toBeNull();
  });

  test("세금 로트 절: 연도 선택(기본 올해) → tax-lots?market&year, 시장별 요약 Stats·실현 로트 표·CSV 링크; 「전체」 면 year 없이", async () => {
    await mount(routes());
    const year = String(new Date().getFullYear());
    const sec = section("tax-lots")!;
    expect(sec.querySelector("h2")!.textContent).toBe("세금 로트");
    const sel = sec.querySelector('select[aria-label="연도"]') as HTMLSelectElement;
    expect(sel.value).toBe(year);
    const first = ff!.calls.filter((c) => c.url.includes("/tax-lots"));
    expect(first.map((c) => new URL(c.url, "http://x").searchParams.get("year"))).toEqual([year, year]);
    // 시장별 블록(KR·US) — 요약 Stats + 실현 표 + CSV
    const blocks = sec.querySelectorAll("[data-tax-market]");
    expect(Array.from(blocks).map((b) => b.getAttribute("data-tax-market"))).toEqual(["KR", "US"]);
    const kr = blocks[0] as HTMLElement;
    const dts = Array.from(kr.querySelectorAll("dl.stats dt")).map((e) => e.textContent);
    expect(dts).toEqual(["실현 손익", "단기", "장기", "워시세일"]);
    expect(kr.textContent).toContain("35,000");
    const t = kr.querySelector("table")!;
    expect(Array.from(t.querySelectorAll("thead th")).map((e) => e.textContent)).toEqual(["종목", "수량", "매수일", "매도일", "원가", "매도금", "손익", "기간", "워시세일"]);
    const row = t.querySelector("tbody")!.querySelectorAll("tr")[1];
    expect(row.textContent).toContain("장기");
    expect(row.querySelector('.chip[data-tone="warn"]')!.textContent).toBe("워시세일");
    expect(kr.textContent).toContain("005930 삼성전자");
    expect((kr.querySelector("a.table-csv") as HTMLAnchorElement).getAttribute("href")).toBe(`/api/accounts/paper-1/tax-lots?market=KR&year=${year}&format=csv`);
    choose(sel, "");
    await flush();
    await flush();
    const after = ff!.calls.filter((c) => c.url.includes("/tax-lots")).slice(2);
    expect(after.length).toBeGreaterThan(0);
    expect(after.every((c) => new URL(c.url, "http://x").searchParams.get("year") === null)).toBe(true);
    expect((kr.querySelector("a.table-csv") as HTMLAnchorElement).getAttribute("href")).toBe("/api/accounts/paper-1/tax-lots?market=KR&format=csv");
  });

  test("파생 계좌는 세금 로트 절이 없다", async () => {
    await mount(routes(), "paper-fo");
    expect(section("tax-lots")).toBeNull();
  });

  test("「‹ 돌아가기」 는 /accounts 로, 헤더에 현금·모드 메모", async () => {
    const { container } = await mount(routes(), "real-1");
    const back = Array.from(container.querySelectorAll("a")).find((a) => a.textContent?.includes("돌아가기"))!;
    expect(back.getAttribute("href")).toBe("/accounts");
    expect(container.textContent).toContain("5,000,000");
    expect(container.textContent).toContain("LIVE 확인 11-01");
  });

  test("파생 계좌는 #fo-positions 에 파생 표, 리스크 절 없음", async () => {
    await mount(routes(), "paper-fo");
    expect(section("positions")).toBeNull();
    expect(section("risk")).toBeNull();
    const fo = section("fo-positions")!;
    expect(fo.hidden).toBe(false);
    expect(fo.textContent).toContain("파생 보유");
    expect(fo.textContent).toContain("A0166000");
    expect(fo.textContent).toContain("350.25");
    expect(fo.textContent).toContain("175,125,000");
    expect(fo.textContent).toContain("2026-11-20");
    // 풋 매수 = 하락 뷰: 종류 PUT → 「풋」, contract_side LONG → 「매수」, direction SHORT → 「하락」
    expect(fo.textContent).toContain("풋");
    expect(fo.textContent).toContain("매수·하락");
    expect(fo.textContent).not.toContain("PUT");
    expect(ff!.calls.some((c) => c.url.startsWith("/api/analytics/fo/report"))).toBe(true);
    expect(ff!.calls.some((c) => c.url.includes("/tax-lots") || c.url.startsWith("/api/positions/risk") || c.url.startsWith("/api/analytics/performance"))).toBe(false);
    expect(ff!.calls.some((c) => c.url.startsWith("/api/quotes/fo") && c.url.includes("codes=A0166000"))).toBe(true);
  });

  test("없는 계좌 id 면 「계좌 없음」", async () => {
    const { container } = await mount(routes(), "nope");
    expect(container.textContent).toContain("계좌 없음");
  });
});
