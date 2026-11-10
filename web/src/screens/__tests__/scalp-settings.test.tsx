import { act } from "react";
import { afterEach, beforeEach, expect, test } from "vitest";
import { ScalpSwitch } from "../system/ScalpSwitch";
import { tokenStore } from "../../lib/api";
import { clearResourceCache } from "../../lib/cache";
import { setEventsClient } from "../../lib/sse";
import { installFakeFetch, type FakeFetch } from "../../test/fakeFetch";
import { flush, render } from "../../test/render";

let ff: FakeFetch | undefined;
beforeEach(() => {
  clearResourceCache();
  tokenStore.set("test-token");
  setEventsClient({ subscribe: () => () => {}, status: () => ({ connected: true, since: 0 }), close() {} });
});
afterEach(() => { ff?.restore(); act(() => tokenStore.set("")); });

const scalp = {
  accounts: [
    { id: "paper-main", env: "paper", markets: ["KR"], enabled: true, source: "yaml", effective: true, reason: null },
    { id: "real-1", env: "real", markets: ["KR"], enabled: false, source: "control", effective: false, reason: "꺼짐" },
  ],
  judge: true, max_positions: 3, max_weight_pct: 0.05, default_paper: true,
};
const patches = () => ff!.calls.filter((c) => c.init?.method === "PATCH").map((c) => ({ url: c.url, body: JSON.parse(String(c.init?.body)) }));
const sw = (c: HTMLElement, id: string) => c.querySelector(`[role="switch"][aria-label="${id} 단타"]`) as HTMLButtonElement;

test("renders rows, paper toggle patches without confirm, real enable needs id, reset sends null", async () => {
  ff = installFakeFetch({
    "GET /api/settings/scalp": scalp,
    "PATCH /api/settings/scalp/paper-main": scalp,
    "PATCH /api/settings/scalp/real-1": scalp,
  });
  const { container } = render(<ScalpSwitch />);
  await flush();
  expect(container.textContent).toContain("paper-main");
  expect(container.textContent).toContain("모의");
  expect(container.textContent).toContain("실전");
  expect(container.textContent).toContain("동작");
  expect(container.textContent).toContain("종목당 비중 5%"); // API 는 비율(0.05)
  expect(container.textContent).toContain("꺼짐");
  await act(async () => sw(container, "paper-main").click());
  await flush();
  expect(patches()).toEqual([{ url: expect.stringContaining("/settings/scalp/paper-main"), body: { enabled: false } }]);
  expect(document.querySelector('[role="dialog"]')).toBeNull();

  await act(async () => sw(container, "real-1").click());
  await flush();
  const dlg = document.querySelector('[role="dialog"]') as HTMLElement;
  expect(dlg).not.toBeNull();
  const input = dlg.querySelector("input") as HTMLInputElement;
  const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")!.set!;
  await act(async () => { setter.call(input, "real-1"); input.dispatchEvent(new Event("input", { bubbles: true })); });
  const ok = Array.from(dlg.querySelectorAll("button")).find((b) => !b.disabled && b.textContent !== "취소") as HTMLButtonElement;
  await act(async () => ok.click());
  await flush();
  expect(patches()[1].body).toEqual({ enabled: true, confirm: "real-1" });

  const reset = Array.from(container.querySelectorAll("button")).find((b) => b.textContent === "기본값으로") as HTMLButtonElement;
  expect(reset).toBeTruthy();
  await act(async () => reset.click());
  await flush();
  expect(patches()[2]).toMatchObject({ url: expect.stringContaining("real-1"), body: { enabled: null } });
});

test("shows error on 400", async () => {
  ff = installFakeFetch({
    "GET /api/settings/scalp": scalp,
    "PATCH /api/settings/scalp/paper-main": () => new Response(JSON.stringify({ detail: "확인 필요" }), { status: 400 }),
  });
  const { container } = render(<ScalpSwitch />);
  await flush();
  await act(async () => sw(container, "paper-main").click());
  await flush();
  expect(container.querySelector('[role="alert"]')?.textContent).toContain("확인 필요");
});

const cell = (c: HTMLElement, id: string, col: number) => {
  const row = Array.from(c.querySelectorAll("tr")).find((r) => r.querySelector(`[role="switch"][aria-label="${id} 단타"]`))!;
  return row.querySelectorAll("td")[col] as HTMLElement;
};

test("real account ON -> OFF sends enabled:false directly (no confirm); reset hidden unless source is control; reason in 실효 cell", async () => {
  const live = {
    accounts: [
      { id: "paper-main", env: "paper", markets: ["KR"], enabled: true, source: "yaml", effective: true, reason: null },
      { id: "real-1", env: "real", markets: ["KR"], enabled: true, source: "default", effective: false, reason: "전체 일시정지" },
    ],
    judge: true, max_positions: 3, max_weight_pct: 0.05, default_paper: true,
  };
  ff = installFakeFetch({
    "GET /api/settings/scalp": live,
    "PATCH /api/settings/scalp/real-1": live,
  });
  const { container } = render(<ScalpSwitch />);
  await flush();
  // 기본값으로 버튼은 source === "control" 일 때만
  expect(Array.from(container.querySelectorAll("button")).some((b) => b.textContent === "기본값으로")).toBe(false);
  // 실효 칸에는 사유 문구가 그대로(스위치의 「꺼짐 · 켜기」 라벨과 별개)
  const eff = cell(container, "real-1", 3);
  expect(eff.textContent).toBe("전체 일시정지");
  expect(cell(container, "paper-main", 3).textContent).toBe("동작");
  await act(async () => sw(container, "real-1").click());
  await flush();
  expect(document.querySelector('[role="dialog"]')).toBeNull();
  expect(patches()).toEqual([{ url: expect.stringContaining("/settings/scalp/real-1"), body: { enabled: false } }]);
});

test("reason text is shown in the 실효 cell for a switched-off account", async () => {
  ff = installFakeFetch({
    "GET /api/settings/scalp": scalp,
  });
  const { container } = render(<ScalpSwitch />);
  await flush();
  expect(cell(container, "real-1", 3).textContent).toBe("꺼짐");
  expect(cell(container, "real-1", 2).textContent).toBe("꺼짐 · 켜기");
});

test("판정자: 켜기는 확인 창 뒤 PATCH /settings/scalp-judge, 화면 설정이면 「판정자 기본값으로」는 null", async () => {
  let cur = { ...scalp, judge: false, judge_source: "yaml", judge_default: false };
  ff = installFakeFetch({
    "GET /api/settings/scalp": () => cur,
    "PATCH /api/settings/scalp-judge": (_u: URL, init?: RequestInit) => {
      const enabled = (JSON.parse(String(init?.body)) as { enabled: boolean | null }).enabled;
      cur = { ...cur, judge: enabled ?? false, judge_source: enabled === null ? "yaml" : "control" };
      return cur;
    },
  });
  const { container } = render(<ScalpSwitch />);
  await flush();
  const line = container.querySelector('[aria-label="단타 판정자"]') as HTMLElement;
  expect(line.textContent).toContain("판정자 꺼짐");
  const on = Array.from(line.querySelectorAll("button")).find((b) => b.textContent === "판정자 켜기") as HTMLButtonElement;
  await act(async () => on.click());
  expect(patches()).toEqual([]);
  const dlg = document.querySelector('[role="dialog"]') as HTMLElement;
  expect(dlg.textContent).toContain("사용량");
  const ok = Array.from(dlg.querySelectorAll("button")).find((b) => b.textContent?.trim() === "확인") as HTMLButtonElement;
  await act(async () => ok.click());
  await flush();
  expect(patches()).toEqual([{ url: expect.stringContaining("/settings/scalp-judge"), body: { enabled: true } }]);
  const line2 = container.querySelector('[aria-label="단타 판정자"]') as HTMLElement;
  expect(line2.textContent).toContain("판정자 켜짐");
  const reset = Array.from(line2.querySelectorAll("button")).find((b) => b.textContent === "판정자 기본값으로") as HTMLButtonElement;
  await act(async () => reset.click());
  await flush();
  expect(patches()[1].body).toEqual({ enabled: null });
});


test("인증이 없으면 변경 전에 설정 링크를 보여주고 모든 변경 버튼을 막는다", async () => {
  act(() => tokenStore.set(""));
  ff = installFakeFetch({ "GET /api/settings/scalp": scalp });
  const { container } = render(<ScalpSwitch />); await flush();
  expect(container.querySelector('a[href="/system/admin#token"]')).not.toBeNull();
  const controls = Array.from(container.querySelectorAll("button"));
  expect(controls.length).toBeGreaterThan(0);
  expect(controls.every((b) => b.disabled)).toBe(true);
  await act(async () => controls[0].click());
  expect(document.querySelector('[role="dialog"]')).toBeNull();
  expect(patches()).toHaveLength(0);
  await act(async () => tokenStore.set("saved-fixture"));
  expect(controls.some((b) => !b.disabled)).toBe(true);
});
