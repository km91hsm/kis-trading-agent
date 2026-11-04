import { act } from "react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, test } from "vitest";
import { Today } from "../today/Today.tsx";
import { tokenStore } from "../../lib/api";
import { clearResourceCache } from "../../lib/cache";
import { setEventsClient, type EventsClient } from "../../lib/sse";
import { installFakeFetch, type FakeFetch } from "../../test/fakeFetch";
import { flush, render } from "../../test/render";
import {
  fixtureAccount,
  fixtureApproval,
  fixtureScalpDecision,
  fixtureSpotDecision,
  fixtureSpotOrder,
  fixtureUsageCaps,
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

  test("partial 응답이면 결정 표 위에 칩", async () => {
    await mount(routes({ "GET /api/decisions": merged([fixtureSpotDecision()], true) }));
    const dec = section("decisions");
    expect(dec.textContent).toContain("일부 레인 응답 없음");
    expect(dec.querySelector('a[href="/decisions/spot/7"]')).not.toBeNull();
  });
});
