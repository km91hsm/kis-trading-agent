import { act } from "react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, test } from "vitest";
import { Accounts } from "../accounts/Accounts.tsx";
import { tokenStore } from "../../lib/api";
import { clearResourceCache } from "../../lib/cache";
import { setEventsClient, type EventsClient } from "../../lib/sse";
import { installFakeFetch, type FakeFetch } from "../../test/fakeFetch";
import { flush, render } from "../../test/render";
import { fixtureAccounts, fixtureFoSummary, fixtureOverview } from "../../test/fixtures/accounts";

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
    "GET /api/accounts/overview": fixtureOverview(),
    "GET /api/accounts": fixtureAccounts(),
    "GET /api/fo/summary": fixtureFoSummary(),
    ...over,
  };
}
const json = (body: unknown, status: number) => new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });

async function mount(r: Routes) {
  ff = installFakeFetch(r);
  const out = render(
    <MemoryRouter>
      <Accounts />
    </MemoryRouter>,
  );
  await flush();
  return out;
}
const section = (id: string) => document.getElementById(id) as HTMLElement;
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
const patches = () => ff!.calls.filter((c) => c.init?.method === "PATCH");
const bodyOf = (c: { init?: RequestInit }) => JSON.parse(String(c.init!.body));
const dialog = () => document.querySelector('[role="dialog"]') as HTMLElement;

describe("계좌·보유 통합 화면", () => {
  test("paused 면 모드 옆 「(전체 일시정지로 새 매수 멈춤)」, 아니면 없음", async () => {
    await mount(routes({ "GET /api/accounts": fixtureAccounts().map((a) => ({ ...a, paused: true })) }));
    expect(document.body.textContent).toContain("(전체 일시정지로 새 매수 멈춤)");
  });

  test("paused 아니면 「(전체 일시정지로 새 매수 멈춤)」 없음", async () => {
    await mount(routes());
    expect(document.body.textContent).not.toContain("전체 일시정지로 새 매수 멈춤");
  });

  test("섹션 컨테이너는 데이터 전에 있고, 합계·환율 줄을 그린다", async () => {
    ff = installFakeFetch(routes());
    render(
      <MemoryRouter>
        <Accounts />
      </MemoryRouter>,
    );
    expect(section("overview")).not.toBeNull();
    expect(section("fo-positions")).not.toBeNull();
    await flush();
    const ov = section("overview");
    expect(ov.textContent).toContain("13,000,000");
    expect(ov.textContent).toContain("+75,000");
    expect(ov.textContent).toContain("2,500.50");
    expect(ov.textContent).toContain("적용 환율 USD/KRW 1,385.50 (11월 4일 종가) → 환산 합계 16,464,443");
  });

  test("fx 가 없으면 「환율 없음」", async () => {
    await mount(routes({ "GET /api/accounts/overview": fixtureOverview({ fx: null }) }));
    const ov = section("overview");
    expect(ov.textContent).toContain("환율 없음");
    expect(ov.textContent).not.toContain("환산 합계");
  });

  test("전역 재개 버튼: 실전 auto 가 있으면 재입력 대화상자 → PATCH /settings/paused {value:false, confirm:'real-1'}; 일시정지는 확인 없이 {value:true}", async () => {
    let paused = true;
    let overviewGets = 0;
    let accountGets = 0;
    await mount(
      routes({
        "GET /api/accounts/overview": () => {
          overviewGets += 1;
          return fixtureOverview({ paused });
        },
        "GET /api/accounts": () => {
          accountGets += 1;
          return fixtureAccounts().map((a) => ({ ...a, paused })); // 전역 paused 는 /accounts 행마다 실린다
        },
        "PATCH /api/settings/paused": (_u: URL, init?: RequestInit) => {
          paused = JSON.parse(String(init!.body)).value;
          return { paused };
        },
      }),
    );
    const ov = section("overview");
    act(() => button(ov, "전체 자동매매 재개").click());
    expect(button(dialog(), "확인").disabled).toBe(true);
    type(dialog().querySelector("input") as HTMLInputElement, "real-1");
    act(() => button(dialog(), "확인").click());
    await flush();
    expect(patches()).toHaveLength(1);
    expect(patches()[0].url).toBe("/api/settings/paused");
    expect(bodyOf(patches()[0])).toEqual({ value: false, confirm: "real-1" });
    expect(overviewGets).toBe(2);
    expect(accountGets).toBe(2);
    expect(dialog()).toBeNull();

    act(() => button(ov, "전체 자동매매 일시정지").click());
    expect(dialog().querySelector("input")).toBeNull();
    act(() => button(dialog(), "확인").click());
    await flush();
    expect(patches()).toHaveLength(2);
    expect(bodyOf(patches()[1])).toEqual({ value: true });
    expect(button(ov, "전체 자동매매 재개")).toBeTruthy();
  });

  test("모의만이면 재개는 confirm 없이 {value:false}; 400 이면 버튼 아래 한 줄 오류", async () => {
    await mount(
      routes({
        "GET /api/accounts/overview": fixtureOverview({ paused: true }),
        "GET /api/accounts": fixtureAccounts()
          .filter((a) => a.env === "paper")
          .map((a) => ({ ...a, paused: true })),
        "PATCH /api/settings/paused": () => json({ detail: "확인이 필요합니다" }, 400),
      }),
    );
    const ov = section("overview");
    act(() => button(ov, "전체 자동매매 재개").click());
    expect(dialog().querySelector("input")).toBeNull();
    act(() => button(dialog(), "확인").click());
    await flush();
    expect(bodyOf(patches()[0])).toEqual({ value: false });
    expect(ov.querySelector('[role="alert"]')!.textContent).toContain("확인이 필요합니다");
  });

  test("계좌 표: 행의 계좌 id 가 /accounts/{id} 로 연결되고 환경·모드·당일·승인 대기를 보인다", async () => {
    const { container } = await mount(routes());
    const links = Array.from(container.querySelectorAll("#overview [data-account-row]")) as HTMLAnchorElement[];
    expect(links.map((a) => a.getAttribute("href"))).toEqual(["/accounts/paper-1", "/accounts/real-1"]);
    expect(container.querySelector("#overview a.account-card")).toBeNull();
    const row = links[1].closest("tr, .row-card") as HTMLElement;
    expect(row.textContent).toContain("실전");
    expect(row.textContent).toContain("자동");
    expect(row.textContent).toContain("-45,000");
    expect(row.querySelector('.chip[data-tone="warn"]')!.textContent).toContain("2");
  });

  test("좁은 폭(390) 행 카드에서는 계좌 이름이 제목 줄(.row-card-title)이다", async () => {
    const orig = window.innerWidth;
    Object.defineProperty(window, "innerWidth", { configurable: true, value: 390 });
    try {
      const { container } = await mount(routes());
      const titles = Array.from(container.querySelectorAll("#overview .row-card .row-card-title")).map((t) => t.textContent);
      expect(titles[0]).toContain("paper-1");
      expect(titles[1]).toContain("real-1");
    } finally {
      Object.defineProperty(window, "innerWidth", { configurable: true, value: orig });
    }
  });

  test("합계 띠는 원화·달러·참고(환율·환산 합계) 세 칸 한 묶음", async () => {
    const { container } = await mount(routes());
    const cells = Array.from(container.querySelectorAll("#overview .kpi-strip [data-kpi]"));
    expect(cells.map((c) => c.getAttribute("data-kpi"))).toEqual(["krw", "usd", "fx"]);
    expect(cells[0].textContent).toContain("원화 합계");
    expect(cells[1].textContent).toContain("달러 합계");
    expect(cells[2].textContent).toContain("환율");
  });

  test("파생 계좌는 #fo-positions 카드 묶음 — 모의 칩·모드·NAV/현금·「계좌별 화면 ›」 링크", async () => {
    await mount(routes());
    const fo = section("fo-positions");
    expect(fo.hidden).toBe(false);
    expect(section("overview").querySelector('a[href="/accounts/paper-fo"]')).toBeNull();
    const link = fo.querySelector('a[href="/accounts/paper-fo"]') as HTMLElement;
    expect(link.textContent).toBe("계좌별 화면 ›");
    expect(fo.textContent).toContain("paper-fo");
    expect(fo.textContent).toContain("모의");
    expect(fo.textContent).toContain("모드 끔");
    // NAV·증거금은 /fo/summary(fo_nav_snapshot) — /accounts 의 nav(50,000,000) 가 아니라 50,500,000
    expect(fo.textContent).toContain("50,500,000원");
    expect(fo.textContent).not.toContain("50,000,000");
    expect(fo.textContent).toContain("증거금");
    expect(fo.textContent).toContain("12,000,000원");
    expect(fo.textContent).toContain("23.76%");
    expect(Array.from(fo.querySelectorAll(".chip")).map((c) => c.textContent)).toContain("기록만");
  });

  test("파생 계좌 state=entries_paused 는 「새 진입 멈춤」 칩", async () => {
    await mount(routes({ "GET /api/fo/summary": fixtureFoSummary({ mode: "auto", paused: true, state: "entries_paused" }) }));
    const chips = Array.from(section("fo-positions").querySelectorAll(".chip")).map((c) => c.textContent);
    expect(chips).toContain("새 진입 멈춤");
    expect(chips).not.toContain("기록만");
  });

  test("/fo/summary 가 실패하면 파생 카드는 /accounts 의 NAV·현금으로 그린다", async () => {
    await mount(routes({ "GET /api/fo/summary": json({ detail: "x" }, 500) }));
    const fo = section("fo-positions");
    expect(fo.textContent).toContain("50,000,000원");
    expect(fo.textContent).toContain("현금");
  });

  test("합계 띠 칸마다 범위(모의 n · 실전 n), 값에 통화 단위", async () => {
    const { container } = await mount(routes());
    const cells = Array.from(container.querySelectorAll("#overview .kpi-strip [data-kpi]"));
    expect(cells[0].textContent).toContain("모의 1 · 실전 1");
    expect(cells[0].textContent).toContain("13,000,000원");
    expect(cells[1].textContent).toContain("모의 1");
    expect(cells[1].textContent).not.toContain("실전");
    expect(cells[1].textContent).toContain("$2,500.50");
  });

  test("계좌 표: 헤더 「순자산」, 값에 통화 단위, 승인 대기 0건은 「없음」(경고 칩 아님)", async () => {
    const { container } = await mount(routes());
    const heads = Array.from(container.querySelectorAll("#overview thead th")).map((t) => t.textContent);
    expect(heads).toContain("순자산");
    expect(heads).not.toContain("NAV");
    const rows = Array.from(container.querySelectorAll("#overview tbody tr"));
    expect(rows[0].textContent).toContain("10,000,000원");
    expect(rows[0].textContent).toContain("없음");
    expect(rows[0].querySelector('.chip[data-tone="warn"]')).toBeNull();
    expect(rows[1].textContent).toContain("2건");
  });

  test("파생 계좌만 있으면 표 대신 「현물 계좌 없음」", async () => {
    await mount(routes({ "GET /api/accounts": fixtureAccounts().filter((a) => a.lane === "fo") }));
    expect(section("overview").textContent).toContain("현물 계좌 없음");
    expect(section("overview").querySelector("table")).toBeNull();
  });

  test("파생 계좌가 없으면 #fo-positions 는 hidden", async () => {
    await mount(routes({ "GET /api/accounts": fixtureAccounts().filter((a) => a.lane !== "fo") }));
    expect(section("fo-positions").hidden).toBe(true);
  });

  test("합계 조회가 500 이어도 전역 「일시정지」는 보이고 PATCH {value:true}", async () => {
    await mount(
      routes({
        "GET /api/accounts/overview": () => json({ detail: "fx broken" }, 500),
        "PATCH /api/settings/paused": { paused: true },
      }),
    );
    const ov = section("overview");
    expect(ov.textContent).toContain("합계를 불러오지 못했습니다");
    act(() => button(ov, "전체 자동매매 일시정지").click());
    act(() => button(dialog(), "확인").click());
    await flush();
    expect(bodyOf(patches()[0])).toEqual({ value: true });
  });

  test("계좌 조회가 실패하면 Empty 로 오류를 보인다", async () => {
    await mount(routes({ "GET /api/accounts": () => json({ detail: "db locked" }, 500) }));
    const ov = section("overview");
    expect(ov.textContent).toContain("계좌를 불러오지 못했습니다");
    expect(ov.textContent).toContain("db locked");
  });
});
