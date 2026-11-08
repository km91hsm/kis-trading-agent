import { act } from "react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";
import { Today } from "../today/Today.tsx";
import { tokenStore } from "../../lib/api";
import { clearResourceCache } from "../../lib/cache";
import { setEventsClient, type EventsClient } from "../../lib/sse";
import { installFakeFetch, type FakeFetch } from "../../test/fakeFetch";
import { flush, render } from "../../test/render";
import {
  fixtureAccount,
  fixtureApproval,
  fixtureDaemon,
  fixtureFoToday,
  fixtureJob,
  fixtureMarketToday,
  fixtureNotice,
  fixtureRiskCheck,
  fixtureScalpDecision,
  fixtureSpotDecision,
  fixtureSpotOrder,
  fixtureUsageCaps,
  fixtureUsApproval,
  merged,
} from "../../test/fixtures/today";

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
    "GET /api/approvals": { approvals: [fixtureApproval()] },
    "GET /api/decisions": merged([fixtureSpotDecision(), fixtureScalpDecision()]),
    "GET /api/orders": merged([fixtureSpotOrder()]),
    "GET /api/accounts": [fixtureAccount(), fixtureAccount({ id: "real-1", env: "real", lane: "spot" })],
    "GET /api/system/usage": fixtureUsageCaps([{ kind: "analyze", used: 1, cap: 3 }]),
    ...over,
  };
}
const json = (body: unknown, status: number) => new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });

async function mount(r: Routes) {
  ff = installFakeFetch(r);
  const out = render(
    <MemoryRouter>
      <Today />
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

describe("오늘 화면", () => {
  test("일시정지 배너: paused 면 문구·경로·재개 링크, 아니면 없음, at 없으면 경로 생략", async () => {
    await mount(routes({ "GET /api/system/pause": { paused: true, at: "2026-11-09T09:00:00+09:00", by: "telegram" } }));
    const b = document.querySelector(".pause-banner") as HTMLElement;
    expect(b.textContent).toContain("자동매매 전체 일시정지 중 · ");
    expect(b.textContent).toContain("텔레그램");
    expect(b.textContent).toContain("계좌 모드(자동/승인)는 그대로, 새 매수·진입만 멈춤(손절·청산은 계속)");
    expect(b.querySelector("a")?.getAttribute("href")).toBe("/accounts");
  });

  test("일시정지 배너: paused=false 면 없음", async () => {
    await mount(routes({ "GET /api/system/pause": { paused: false, at: null, by: null } }));
    expect(document.querySelector(".pause-banner")).toBeNull();
  });

  test("일시정지 배너: at 이 없으면 시각·경로 생략", async () => {
    await mount(routes({ "GET /api/system/pause": { paused: true, at: null, by: null } }));
    const b = document.querySelector(".pause-banner") as HTMLElement;
    expect(b.textContent).toContain("전체 일시정지 중");
    expect(b.textContent).not.toContain("▲ 메뉴");
    expect(b.textContent).not.toContain(" · ");
  });

  test("레인 카드 paused 배지에 「계좌 모드와 별개」 설명", async () => {
    await mount(routes({ "GET /api/accounts": [fixtureAccount({ paused: true })] }));
    const lane = section("spot-lane");
    expect(lane.textContent).toContain("전체 자동매매 일시정지 — 새 매수만 멈춤, 계좌 모드와 별개");
    expect(lane.querySelector(".lane-note")?.textContent).toBe("전체 자동매매 일시정지 — 새 매수만 멈춤, 계좌 모드와 별개");
    expect(lane.querySelector("[title]")).toBeNull(); // 보이는 줄만 — title 중복 없음
  });

  test("섹션 컨테이너는 데이터 전에 있고, 계좌 없는 레인은 hidden", async () => {
    ff = installFakeFetch(routes());
    render(
      <MemoryRouter>
        <Today />
      </MemoryRouter>,
    );
    for (const id of ["approvals", "spot-lane", "scalp-lane", "fo-lane", "decisions"]) expect(section(id)).not.toBeNull();
    await flush();
    expect(section("spot-lane").hidden).toBe(false);
    expect(section("scalp-lane").hidden).toBe(true);
    expect(section("fo-lane").hidden).toBe(true);
    expect(section("spot-lane").textContent).toContain("오늘 analyze 1/3");
  });

  test("대기 승인이 있으면 최상단에 승인·거절 버튼과 근거 요약을 그린다", async () => {
    const { container } = await mount(routes());
    const sections = Array.from(container.querySelectorAll("section[id]"));
    expect(sections[0].id).toBe("approvals");
    const ap = section("approvals");
    expect(ap.hidden).toBe(false);
    expect(ap.textContent).toContain("실적 상향·외국인 순매수");
    expect(ap.textContent).toContain("005930");
    expect(button(ap, "승인")).toBeTruthy();
    expect(button(ap, "거절")).toBeTruthy();
  });

  test("대기 승인 행·확인 본문에 금액(주문 시장 통화 row.currency)과 결정 상세 링크", async () => {
    await mount(
      routes({
        "GET /api/approvals": { approvals: [fixtureApproval(), fixtureUsApproval()] },
        // 원화 계좌로 미국 주식을 사도 주문 통화는 USD — 계좌 currency 가 아니라 row.currency 를 쓴다
        "GET /api/accounts": [fixtureAccount(), fixtureAccount({ id: "paper-us", currency: "KRW", markets: ["KR", "US"] })],
      }),
    );
    const ap = section("approvals");
    const [kr, us] = Array.from(ap.querySelectorAll("li.approval")) as HTMLElement[];
    expect(kr.textContent).toContain("210,000원");
    expect(kr.querySelector('a[href="/decisions/spot/7"]')!.textContent).toContain("근거 전문");
    expect(us.textContent).toContain("$461.00");
    expect(us.querySelector("a")).toBeNull();
    act(() => button(kr, "승인").click());
    const dialog = document.querySelector('[role="dialog"]') as HTMLElement;
    expect(dialog.textContent).toContain("매수 3주 · 210,000원");
  });

  test("날짜를 고르지 않았으면 「오늘」은 KST 자정에 넘어가 다음 날로 다시 읽는다", async () => {
    vi.useFakeTimers({ toFake: ["Date", "setInterval", "clearInterval"] });
    try {
      vi.setSystemTime(new Date("2026-11-05T14:59:30Z")); // KST 23:59:30
      await mount(routes());
      const dates = () => ff!.calls.filter((c) => c.url.startsWith("/api/decisions")).map((c) => new URL(c.url, "http://x").searchParams.get("date"));
      expect(dates()).toEqual(["2026-11-05"]);
      vi.setSystemTime(new Date("2026-11-05T15:00:30Z")); // KST 다음 날 00:00:30
      act(() => vi.advanceTimersByTime(60_000));
      await flush();
      expect(dates().at(-1)).toBe("2026-11-06");
      expect((document.querySelector('input[type="date"]') as HTMLInputElement).value).toBe("2026-11-06");
    } finally {
      vi.useRealTimers();
    }
  });

  test("대기 승인이 없으면 approvals 섹션은 hidden", async () => {
    await mount(routes({ "GET /api/approvals": { approvals: [] } }));
    expect(section("approvals").hidden).toBe(true);
  });

  test("실전 승인은 계좌 id 재입력 뒤 PATCH /approvals/{id} {action:'approve', confirm:'real-1'}", async () => {
    let decided = false;
    await mount(
      routes({
        "GET /api/approvals": () => ({ approvals: [fixtureApproval({ id: 12, account_id: "real-1", env: "real" })] }),
        "PATCH /api/approvals/12": () => {
          decided = true;
          return json({ status: "accepted", request_id: 99 }, 202);
        },
      }),
    );
    const ap = section("approvals");
    act(() => button(ap, "승인").click());
    const dialog = document.querySelector('[role="dialog"]') as HTMLElement;
    expect(button(dialog, "확인").disabled).toBe(true);
    type(dialog.querySelector("input") as HTMLInputElement, "real-1");
    act(() => button(dialog, "확인").click());
    await flush();
    expect(decided).toBe(true);
    expect(patches()).toHaveLength(1);
    expect(patches()[0].url).toBe("/api/approvals/12");
    expect(bodyOf(patches()[0])).toEqual({ action: "approve", confirm: "real-1" });
    expect(ap.textContent).toContain("집행 대기");
    expect(document.querySelector('[role="dialog"]')).toBeNull();
  });

  test("모의 승인은 confirm 없이 PATCH", async () => {
    await mount(routes({ "PATCH /api/approvals/11": () => json({ status: "accepted", request_id: 5 }, 202) }));
    const ap = section("approvals");
    act(() => button(ap, "승인").click());
    const dialog = document.querySelector('[role="dialog"]') as HTMLElement;
    expect(dialog.querySelector("input")).toBeNull();
    act(() => button(dialog, "확인").click());
    await flush();
    expect(patches()).toHaveLength(1);
    expect(bodyOf(patches()[0])).toEqual({ action: "approve" });
    expect(ap.textContent).toContain("집행 대기");
  });

  test("거절 200 이면 다시 읽고, 400 confirm_required 면 error 문구를 보인다", async () => {
    let gets = 0;
    await mount(
      routes({
        "GET /api/approvals": () => {
          gets += 1;
          return { approvals: [fixtureApproval()] };
        },
        "PATCH /api/approvals/11": (_u: URL, init?: RequestInit) =>
          JSON.parse(String(init!.body)).action === "reject"
            ? { ok: true, approval_id: 11, status: "rejected" }
            : json({ detail: { ok: false, approval_id: 11, status: "confirm_required", error: "실전 계좌예요 — 확인 필요" } }, 400),
      }),
    );
    const ap = section("approvals");
    act(() => button(ap, "거절").click());
    act(() => button(document.querySelector('[role="dialog"]')!, "확인").click());
    await flush();
    expect(bodyOf(patches()[0])).toEqual({ action: "reject" });
    expect(gets).toBe(2);
    act(() => button(ap, "승인").click());
    act(() => button(document.querySelector('[role="dialog"]')!, "확인").click());
    await flush();
    expect(ap.textContent).toContain("실전 계좌예요 — 확인 필요");
    expect(ap.textContent).not.toContain("집행 대기");
  });

  test("날짜를 바꾸면 /decisions?date= 와 /orders?date= 를 다시 부른다", async () => {
    const { container } = await mount(routes());
    const input = container.querySelector('input[type="date"]') as HTMLInputElement;
    type(input, "2026-11-04");
    await flush();
    const urls = ff!.calls.map((c) => c.url);
    expect(urls).toContain("/api/decisions?date=2026-11-04");
    expect(urls).toContain("/api/orders?date=2026-11-04");
    expect(section("decisions").querySelector("a[download]")!.getAttribute("href")).toBe("/api/decisions?date=2026-11-04&format=csv");
  });

  test("승인 조회가 실패하면 섹션을 숨기지 않고 오류 문구를 보인다", async () => {
    await mount(routes({ "GET /api/approvals": () => json({ detail: "db locked" }, 500) }));
    const ap = section("approvals");
    expect(ap.hidden).toBe(false);
    expect(ap.textContent).toContain("승인 대기를 불러오지 못했습니다");
    expect(ap.textContent).toContain("db locked");
  });

  test("주문 조회가 실패하면 레인 카드에 「완료」 대신 「불러오지 못함」", async () => {
    await mount(routes({ "GET /api/orders": () => json({ detail: "boom" }, 500) }));
    const spot = section("spot-lane");
    expect(spot.hidden).toBe(false);
    expect(spot.textContent).not.toContain("완료");
    expect(spot.textContent).toContain("불러오지 못함");
    expect(spot.textContent).not.toContain("미체결 0");
  });

  test("주문·결정이 오기 전에는 레인 카드에 「불러오는 중」", async () => {
    await mount(routes({ "GET /api/orders": () => new Promise(() => {}) }));
    const spot = section("spot-lane");
    expect(spot.textContent).not.toContain("완료");
    expect(spot.textContent).toContain("불러오는 중");
  });

  test("결정 표: 「종목」 칸은 코드+이름 링크, 「내용」 칸은 최종 의견 요약 — HOLD 같은 판단은 방향 열에만", async () => {
    await mount(
      routes({
        "GET /api/decisions": merged([
          fixtureSpotDecision({ id: 7, name: "삼성전자", summary: "실적 상향, 외국인 순매수 지속" }),
          fixtureSpotDecision({ id: 9, ticker: "035420", name: "NAVER", action: "HOLD", side: null, summary: "밸류에이션 부담, 관망", at: "2026-11-05T00:30:00+00:00" }),
        ]),
      }),
    );
    const dec = section("decisions");
    const heads = Array.from(dec.querySelectorAll("thead th")).map((e) => e.textContent);
    expect(heads).toContain("종목");
    expect(heads).toContain("내용");
    expect(heads).not.toContain("종목·내용");
    const link = dec.querySelector('a[href="/decisions/spot/7"]')!;
    expect(link.textContent).toBe("005930 삼성전자");
    expect(dec.textContent).toContain("실적 상향, 외국인 순매수 지속");
    const holdRow = Array.from(dec.querySelectorAll("tbody tr")).find((tr) => tr.textContent?.includes("035420"))!;
    expect(holdRow.textContent).toContain("035420 NAVER");
    expect(holdRow.textContent).toContain("보류");
    expect(holdRow.textContent).not.toContain("HOLD");
    expect(holdRow.textContent).toContain("밸류에이션 부담, 관망");
  });

  test("결정이 오기 전에는 결정 절에 「불러오는 중」 — 「이 날 결정이 없습니다」가 아니다", async () => {
    await mount(routes({ "GET /api/decisions": () => new Promise(() => {}) }));
    const dec = section("decisions");
    expect(dec.textContent).toContain("불러오는 중");
    expect(dec.textContent).not.toContain("이 날 결정이 없습니다");
  });

  test("결정 표의 「내용」 칸이 말줄임(long) 열이고 종목 칸은 아니다 — 긴 요약이 행을 여러 줄로 늘리지 않게", async () => {
    const long = "반도체 업황 회복으로 비중 확대를 제안하며 외국인 순매수와 실적 상향이 이어지고 있어 상승 여력이 남아 있다고 본다";
    await mount(routes({ "GET /api/decisions": merged([fixtureSpotDecision({ id: 7, name: "삼성전자", summary: long })]) }));
    const dec = section("decisions");
    const heads = Array.from(dec.querySelectorAll("thead th"));
    const longHeads = heads.filter((th) => th.classList.contains("cell-long")).map((th) => th.textContent);
    expect(longHeads).toEqual(["내용"]);
    const cell = dec.querySelector("tbody td.cell-long") as HTMLElement;
    expect(cell.querySelector(".cell-ellipsis")!.textContent).toBe(long);
    expect(cell.getAttribute("title")).toBe(long);
  });

  test("승인 카드의 종목은 코드+이름", async () => {
    await mount(routes({ "GET /api/approvals": { approvals: [fixtureApproval({ name: "삼성전자" })] } }));
    expect(section("approvals").querySelector(".approval-head strong")!.textContent).toBe("005930 삼성전자");
  });

  test("partial 응답이면 결정 표 위에 칩", async () => {
    await mount(routes({ "GET /api/decisions": merged([fixtureSpotDecision()], true) }));
    const dec = section("decisions");
    expect(dec.textContent).toContain("일부 레인 응답 없음");
    expect(dec.querySelector('a[href="/decisions/spot/7"]')).not.toBeNull();
  });
});

describe("배치", () => {
  test("현황 페이지 절 순서: 승인 → 레인 카드 → 결정 — 기록(주문·체결·다음 할 일·알림)은 /log 페이지", async () => {
    const { container } = await mount(routes());
    expect(container.querySelector(".section-pair")).toBeNull();
    expect(Array.from(container.querySelectorAll("section.screen-section[id]")).map((e) => e.id)).toEqual([
      "approvals", "spot-lane", "scalp-lane", "fo-lane", "pipeline", "decisions", "risk-checks",
    ]);
    expect(container.querySelector("h1")?.textContent).toBe("오늘");
    expect(Array.from(container.querySelectorAll(".page-tabs a")).map((e) => [e.textContent, e.getAttribute("href")])).toEqual([
      ["현황", "/"],
      ["기록", "/log"],
    ]);
  });

  test("기록 페이지(page=log)는 주문·체결 → 다음 할 일 → 알림, h1 「오늘 · 기록」, 날짜 선택기 있음", async () => {
    ff = installFakeFetch(routes());
    const { container } = render(
      <MemoryRouter initialEntries={["/log"]}>
        <Today page="log" />
      </MemoryRouter>,
    );
    await flush();
    expect(Array.from(container.querySelectorAll("section.screen-section[id]")).map((e) => e.id)).toEqual(["orders-timeline", "next-jobs", "notices"]);
    expect(container.querySelector("h1")?.textContent).toBe("오늘 · 기록");
    expect(container.querySelector('input[type="date"]')).not.toBeNull();
    expect(ff!.calls.map((c) => c.url)).not.toContain("/api/decisions?date=" + new Date().toISOString().slice(0, 10));
  });

  test("알림도 고른 날짜로 부른다(/system/notices?date=) — 「오늘 요약」 알림 수가 그날 건수가 되게", async () => {
    ff = installFakeFetch(routes({ "GET /api/system/notices": [fixtureNotice()] }));
    render(
      <MemoryRouter initialEntries={["/?date=2026-11-04"]}>
        <Today />
      </MemoryRouter>,
    );
    await flush();
    const urls = ff!.calls.map((c) => new URL(c.url, "http://x")).filter((u) => u.pathname === "/api/system/notices");
    expect(urls.length).toBeGreaterThan(0);
    expect(urls.at(-1)!.searchParams.get("date")).toBe("2026-11-04");
    expect(urls.at(-1)!.searchParams.get("limit")).toBe("200");
  });

  test("날짜는 ?date= 로 두 페이지가 같이 쓴다 — /?date=2026-11-04 로 들어오면 그 날을 읽고, 고르면 주소가 바뀐다", async () => {
    ff = installFakeFetch(routes());
    const { container } = render(
      <MemoryRouter initialEntries={["/?date=2026-11-04"]}>
        <Today />
      </MemoryRouter>,
    );
    await flush();
    expect(ff!.calls.map((c) => c.url)).toContain("/api/decisions?date=2026-11-04");
    expect((container.querySelector('input[type="date"]') as HTMLInputElement).value).toBe("2026-11-04");
    expect(container.querySelector('.page-tabs a[href="/log?date=2026-11-04"]')).not.toBeNull();
    type(container.querySelector('input[type="date"]') as HTMLInputElement, "2026-11-03");
    await flush();
    expect(ff!.calls.map((c) => c.url)).toContain("/api/decisions?date=2026-11-03");
    expect(container.querySelector('.page-tabs a[href="/log?date=2026-11-03"]')).not.toBeNull();
  });

  test("「오늘 요약」 KPI 띠: 결정·주문·체결·실패·알림 건수가 레인 카드 위에", async () => {
    const { container } = await mount(
      routes({
        "GET /api/orders": merged([fixtureSpotOrder({ status: "filled" }), fixtureSpotOrder({ id: 2, status: "failed", filled_qty: 0 })]),
        "GET /api/system/notices": [fixtureNotice(), fixtureNotice({ id: 2 }), fixtureNotice({ id: 3 })],
      }),
    );
    const kpi = container.querySelector(".today-summary") as HTMLElement;
    expect(kpi).not.toBeNull();
    const stat = (id: string) => kpi.querySelector(`[data-stat="${id}"] dd`)?.textContent;
    expect([stat("decisions"), stat("orders"), stat("fills"), stat("failed"), stat("notices")]).toEqual(["2", "2", "1", "1", "3"]);
    // 레인 카드보다 앞
    expect(kpi.compareDocumentPosition(section("spot-lane")) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });

  test("레인 카드는 Stats 격자(결정·주문·다음 예정) — 다음 예정은 데몬 사용자 잡의 next_at, 「완료」 대신 「처리 완료」", async () => {
    await mount(routes({ "GET /api/system/daemon": fixtureDaemon([fixtureJob({ id: "kr_scan_analyze", kind: "scan_analyze", next_at: "2026-11-05T22:30:00+00:00" })]) }));
    const spot = section("spot-lane");
    expect(spot.querySelector("dl.stats")).not.toBeNull();
    expect(spot.querySelector("dl.lane-stats")).toBeNull();
    expect(spot.querySelector('[data-stat="next"] dd')?.textContent).toContain("07:30");
    expect(spot.textContent).toContain("처리 완료");
    expect(spot.textContent!.replace(/처리 완료/g, "")).not.toContain("완료");
  });

  test("결정 0건이면 CSV 링크를 숨긴다", async () => {
    await mount(routes({ "GET /api/decisions": merged([]) }));
    expect(section("decisions").querySelector("a[download]")).toBeNull();
    expect(section("decisions").textContent).toContain("이 날 결정이 없습니다");
  });

  test("아침 파이프라인: 시장마다 단계 칩(스캔·시장 세션·분석)과 종목 표(코드+이름·단계), 발굴 줄; US 처럼 빈 날은 「기록 없음」", async () => {
    await mount(routes({ "GET /api/markets/KR/today": fixtureMarketToday("KR"), "GET /api/markets/US/today": fixtureMarketToday("US") }));
    const sec = section("pipeline");
    expect(sec.hidden).toBe(false);
    const kr = sec.querySelector('[data-market="KR"]') as HTMLElement;
    expect(Array.from(kr.querySelectorAll(".pipeline-steps .chip")).map((c) => [c.textContent, c.getAttribute("data-tone")])).toEqual([
      ["스캔", "ok"], ["시장 세션", "ok"], ["분석 2종목 · 결정 1", "ok"],
    ]);
    expect(kr.textContent).toContain("발굴 005930 삼성전자 돌파(실적) · 000660 수급");
    expect(kr.textContent).toContain("뉴스 발굴 요청 1건");
    // long 열은 말풍선(Tooltip)이 같은 글을 한 번 더 넣으므로 보이는 span 만 읽는다
    const cells = Array.from(kr.querySelectorAll("tbody tr")).map((tr) =>
      Array.from(tr.querySelectorAll("td")).map((td) => (td.querySelector(".cell-ellipsis") ?? td).textContent),
    );
    expect(cells).toEqual([["005930 삼성전자", "리스크 게이트", "5"], ["000660", "애널리스트", "2"]]);
    const us = sec.querySelector('[data-market="US"]') as HTMLElement;
    expect(us.textContent).toContain("기록 없음");
    expect(ff!.calls.map((c) => c.url)).toContain("/api/markets/KR/today?date=" + new Date().toISOString().slice(0, 10));
  });

  test("리스크 게이트: 0건이면 hidden, 있으면 표(시각·계좌·종목·결과 칩·수량 조정·사유) + 결정 링크", async () => {
    const a = await mount(routes({ "GET /api/risk-checks": [] }));
    expect(section("risk-checks").hidden).toBe(true);
    a.unmount();
    clearResourceCache();
    await mount(routes({ "GET /api/risk-checks": [fixtureRiskCheck(), fixtureRiskCheck({ id: 2, approved: true, adjusted_qty: 3, reasons: [], decision_id: 9 })] }));
    const sec = section("risk-checks");
    expect(sec.hidden).toBe(false);
    const rows = Array.from(sec.querySelectorAll("tbody tr"));
    expect(rows).toHaveLength(2);
    expect(rows[0].textContent).toContain("paper-1");
    expect(rows[0].textContent).toContain("005930 삼성전자");
    expect(rows[0].querySelector('.chip[data-tone="fail"]')?.textContent).toBe("거부");
    expect(rows[0].textContent).toContain("주간 한도 · 현금 부족");
    expect(rows[0].querySelector('a[href="/decisions/spot/1"]')).not.toBeNull();
    expect(rows[1].querySelector('.chip[data-tone="ok"]')?.textContent).toBe("통과");
    expect(rows[1].textContent).toContain("3주");
  });

  test("파생 레인 카드: /fo/today?date= 의 아침 결정 한 줄·재평가 횟수·계좌 칩(손실 한도·진입 대기)", async () => {
    await mount(
      routes({
        "GET /api/accounts": [fixtureAccount(), fixtureAccount({ id: "paper-fo", lane: "fo", markets: ["KR"] })],
        "GET /api/fo/today": fixtureFoToday(),
      }),
    );
    const fo = section("fo-lane");
    expect(fo.hidden).toBe(false);
    expect(fo.textContent).toContain("아침 결정 진입 롱 · 선물 · 확신 70%");
    expect(fo.textContent).toContain("재평가 1회");
    const chips = Array.from(fo.querySelectorAll(".lane-extra .chip")).map((c) => c.textContent);
    expect(chips).toEqual(["paper-fo 손실 한도", "paper-fo 진입 대기"]);
    expect(ff!.calls.map((c) => c.url)).toContain("/api/fo/today?date=" + new Date().toISOString().slice(0, 10));
  });
});
