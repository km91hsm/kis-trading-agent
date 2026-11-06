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

describe("계좌별 화면", () => {
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
    const mode = section("mode")!;
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
    const mode = section("mode")!;
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
    expect(pos.textContent).toContain("평가 합계(KRW) 710,000 · 손익 +10,000");
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
    const orders = ff!.calls.find((c) => c.url.startsWith("/api/orders"))!;
    expect(orders.url).toContain("account_id=paper-1");
  });

  test("리스크 카드: limits 가 있으면 「한도 대비」 막대(한도 초과는 강조)와 기여 상위 3 을 함께, 자료 부족이어도 한도는 그린다", async () => {
    const limits = { max_position_pct: 0.25, max_positions: 5, max_sector_pct: 0.4, min_cash_pct: 0.1, daily_loss_stop_pct: -0.05 };
    const usage = { position_pct_max: 0.3, positions: 3, sector_pct_max: 0.2, cash_pct: 0.05, daily_loss_pct: null };
    const first = await mount(routes({ "GET /api/positions/risk": fixtureRisk({ limits, usage }) }));
    const risk = section("risk")!;
    expect(risk.textContent).toContain("한도 대비");
    const rows = Array.from(risk.querySelectorAll("li.risk-limit")) as HTMLElement[];
    expect(rows).toHaveLength(5);
    expect(risk.querySelectorAll(".risk-contrib li")).toHaveLength(3); // 한도 막대와 기여 상위 3 을 함께(T16-R2)
    expect(risk.querySelectorAll(".risk-bar")).toHaveLength(5 + 3);
    const over = rows.filter((r) => r.classList.contains("over")).map((r) => r.querySelector(".risk-ticker")!.textContent);
    expect(over).toEqual(["최대 종목 비중", "현금 비중"]); // 30% > 25%, 현금 5% < 최소 10%
    expect(rows[0].textContent).toContain("30.00% / 25.00%");
    expect(rows[1].textContent).toContain("3 / 5");
    expect(rows[4].textContent).toContain("— / -5.00%");
    first.unmount();
    ff!.restore();
    clearResourceCache();
    await mount(routes({ "GET /api/positions/risk": fixtureRisk({ insufficient: "종가 부족(3일)", limits, usage }) }));
    const thin = section("risk")!;
    expect(thin.textContent).toContain("자료 부족: 종가 부족(3일)");
    expect(thin.querySelectorAll("li.risk-limit")).toHaveLength(5);
  });

  test("주문 표 「종목」 칸은 코드+이름", async () => {
    await mount(routes({ "GET /api/orders": { items: [fixtureSpotOrder({ name: "삼성전자" })], partial: false } }));
    const orders = section("orders")!;
    expect(orders.textContent).toContain("005930 삼성전자");
  });

  test("「‹ 돌아가기」 는 /accounts 로, 헤더에 현금·모드 메모", async () => {
    const { container } = await mount(routes(), "real-1");
    const back = Array.from(container.querySelectorAll("a")).find((a) => a.textContent?.includes("돌아가기"))!;
    expect(back.getAttribute("href")).toBe("/accounts");
    expect(container.textContent).toContain("현금 5,000,000");
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
