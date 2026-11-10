// 매매 정책 「파생」 계좌별 스위치 — 모의는 확인 창 뒤 켜기(자동 주문 시작), 끄기·기본값은 바로, 실전은 버튼 없음.
import { act } from "react";
import { afterEach, beforeEach, expect, test } from "vitest";
import { FoSwitch } from "../system/FoSwitch";
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
afterEach(() => { ff?.restore(); tokenStore.set(""); document.body.innerHTML = ""; });

type Row = { id: string; env: string; enabled: boolean; source: string; effective: boolean; reason: string | null };
const fo: { accounts: Row[]; default_paper: boolean; product: string; vehicles: string[]; max_contracts: number } = {
  accounts: [
    { id: "paper-fo", env: "paper", enabled: false, source: "yaml", effective: false, reason: "꺼짐" },
    { id: "real-fo", env: "real", enabled: false, source: "default", effective: false, reason: "실전 파생은 항상 꺼짐" },
  ],
  default_paper: false, product: "mini", vehicles: ["FUTURES"], max_contracts: 4,
};
const patches = () => ff!.calls.filter((c) => c.init?.method === "PATCH").map((c) => ({ url: c.url, body: JSON.parse(String(c.init?.body)) }));
const sw = (c: HTMLElement, id: string) => c.querySelector(`[role="switch"][aria-label="${id} 파생"]`) as HTMLButtonElement | null;

test("파생 스위치: 모의 켜기는 확인 뒤 PATCH, 실전은 스위치 없음, 화면 설정이면 기본값으로", async () => {
  let cur = fo;
  const on = { ...fo, accounts: [{ ...fo.accounts[0], enabled: true, source: "control", effective: true, reason: null }, fo.accounts[1]] };
  ff = installFakeFetch({
    "GET /api/settings/fo": () => cur,
    "PATCH /api/settings/fo/paper-fo": () => { cur = on; return on; },
  });
  const { container } = render(<FoSwitch />);
  await flush();
  const sec = container.querySelector('[aria-label="파생 계좌별 스위치"]') as HTMLElement;
  expect(sec.textContent).toContain("paper-fo");
  expect(sec.textContent).toContain("실전 파생은 항상 꺼짐");
  expect(sw(sec, "real-fo")).toBeNull();
  await act(async () => sw(sec, "paper-fo")!.click());
  expect(patches()).toEqual([]);
  const dlg = document.querySelector('[role="dialog"]') as HTMLElement;
  expect(dlg.textContent).toContain("자동");
  const ok = Array.from(dlg.querySelectorAll("button")).find((b) => b.textContent?.trim() === "확인") as HTMLButtonElement;
  await act(async () => ok.click());
  await flush();
  expect(patches()).toEqual([{ url: expect.stringContaining("/settings/fo/paper-fo"), body: { enabled: true } }]);
  const reset = Array.from(sec.querySelectorAll("button")).find((b) => b.textContent === "기본값으로") as HTMLButtonElement;
  await act(async () => reset.click());
  await flush();
  expect(patches()[1].body).toEqual({ enabled: null });
});
