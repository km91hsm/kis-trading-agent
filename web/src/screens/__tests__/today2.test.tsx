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
  fixtureDaemon,
  fixtureJob,
  fixtureNotice,
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

const NOTICES = [
  fixtureNotice({ id: 3, kind: "fill", text: "체결 — 005930 3주", ok: true }),
  fixtureNotice({ id: 2, kind: "approval", text: "승인 요청 — 005930 매수 3주", ok: false }),
  fixtureNotice({ id: 1, kind: "fill", text: "체결 — AAPL 1주", ok: true }),
];

type Routes = Parameters<typeof installFakeFetch>[0];
function routes(over: Routes = {}): Routes {
  return {
    "GET /api/approvals": { approvals: [] },
    "GET /api/decisions": merged([]),
    "GET /api/orders": merged([
      fixtureSpotOrder({
        qty: 10,
        filled_qty: 10,
        fills: [
          { qty: 6, price: "70100", filled_at: "2026-11-05T01:05:07+00:00" },
          { qty: 4, price: "70200", filled_at: "2026-11-05T01:03:09+00:00" },
        ],
      }),
    ], true),
    "GET /api/accounts": [fixtureAccount()],
    "GET /api/system/usage": fixtureUsageCaps([]),
    "GET /api/system/notices": (url: URL) => {
      const kind = url.searchParams.get("kind");
      return kind ? NOTICES.filter((n) => n.kind === kind) : NOTICES;
    },
    "GET /api/system/daemon": fixtureDaemon([
      fixtureJob({ id: 1, kind: "analyze", next_at: "2026-11-05T03:00:00+00:00" }),
      fixtureJob({ id: 2, kind: "scalp", next_at: "2026-11-05T01:30:00+00:00", last_status: "failed" }),
    ]),
    ...over,
  };
}

async function mount(r: Routes) {
  ff = installFakeFetch(r);
  render(
    <MemoryRouter>
      <Today />
    </MemoryRouter>,
  );
  await flush();
}
const section = (id: string) => document.getElementById(id) as HTMLElement;
const lines = (root: ParentNode, sel: string) => Array.from(root.querySelectorAll(sel)).map((e) => e.textContent ?? "");

describe("오늘 화면 ② — 주문·체결·알림·다음 할 일", () => {
  test("섹션 컨테이너는 데이터 전에 있다; 타임라인 3줄(시:분:초) + 일부 레인 칩, /orders 는 한 번만", async () => {
    ff = installFakeFetch(routes());
    render(
      <MemoryRouter>
        <Today />
      </MemoryRouter>,
    );
    for (const id of ["orders-timeline", "notices", "next-jobs"]) expect(section(id)).not.toBeNull();
    expect(section("orders-timeline").textContent).toContain("불러오는 중");
    await flush();
    const t = lines(section("orders-timeline"), ".timeline-row");
    expect(t).toHaveLength(3);
    expect(t[0]).toContain("10:01:00");
    expect(t[0]).toContain("매수 005930 10주 @70,000 · 접수");
    expect(t[1]).toContain("10:03:09");
    expect(t[1]).toContain("체결 4주 @70,200");
    expect(t[2]).toContain("10:05:07");
    expect(section("orders-timeline").textContent).toContain("일부 레인 응답 없음");
    expect(ff.calls.filter((c) => new URL(c.url, "http://x").pathname === "/api/orders")).toHaveLength(1);
  });

  test("알림 피드 kind 필터 칩을 누르면 /system/notices?kind=approval 로 다시 부른다", async () => {
    await mount(routes());
    const chips = Array.from(section("notices").querySelectorAll<HTMLButtonElement>("button[data-notice-kind]"));
    expect(chips.map((b) => b.textContent)).toEqual(["전체", "fill", "approval"]);
    expect(section("notices").querySelectorAll(".notice-row")).toHaveLength(3);
    act(() => chips[2].click());
    await flush();
    const urls = ff!.calls.map((c) => new URL(c.url, "http://x")).filter((u) => u.pathname === "/api/system/notices");
    expect(urls.at(-1)!.searchParams.get("kind")).toBe("approval");
    expect(urls.at(-1)!.searchParams.get("limit")).toBe("200");
    expect(section("notices").querySelectorAll(".notice-row")).toHaveLength(1);
    // 필터 중에도 칩 목록은 유지되고, 누른 칩이 눌림 상태
    const after = Array.from(section("notices").querySelectorAll<HTMLButtonElement>("button[data-notice-kind]"));
    expect(after.map((b) => b.textContent)).toEqual(["전체", "fill", "approval"]);
    expect(after[2].getAttribute("aria-pressed")).toBe("true");
  });

  test("ok=false 알림은 '전송 실패' 칩", async () => {
    await mount(routes());
    const rows = Array.from(section("notices").querySelectorAll(".notice-row"));
    const failed = rows.filter((r) => r.querySelector('.chip[data-tone="fail"]')?.textContent === "전송 실패");
    expect(failed).toHaveLength(1);
    expect(failed[0].textContent).toContain("승인 요청 — 005930 매수 3주");
    expect(failed[0].textContent).toContain("10:02:00");
    expect(failed[0].textContent).toContain("paper-1");
  });

  test("다음 할 일: next_at 순, 마지막 실패 표시; 데몬 꺼짐이면 한 줄", async () => {
    await mount(routes());
    expect(lines(section("next-jobs"), ".job-row")).toEqual(["scalp · 10:30:00 (마지막 실패)", "analyze · 12:00:00"]);
  });

  test("데몬 꺼짐이면 '데몬 꺼짐' 한 줄", async () => {
    await mount(routes({ "GET /api/system/daemon": fixtureDaemon([fixtureJob()], { alive: false }) }));
    expect(section("next-jobs").textContent).toContain("데몬 꺼짐");
    expect(section("next-jobs").querySelectorAll(".job-row")).toHaveLength(0);
  });

  test("조회 실패는 섹션 안에 '불러오지 못했습니다' + 메시지", async () => {
    const boom = () => new Response(JSON.stringify({ detail: "db down" }), { status: 500, headers: { "Content-Type": "application/json" } });
    await mount(routes({ "GET /api/orders": boom, "GET /api/system/notices": boom, "GET /api/system/daemon": boom }));
    for (const id of ["orders-timeline", "notices", "next-jobs"]) {
      expect(section(id).textContent).toContain("불러오지 못했습니다");
      expect(section(id).textContent).toContain("db down");
    }
  });
});
