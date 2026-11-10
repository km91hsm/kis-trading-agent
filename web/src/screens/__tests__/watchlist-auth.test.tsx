import { act } from "react";
import { afterEach, beforeEach, expect, test } from "vitest";
import { Watchlist } from "../data/Watchlist";
import { tokenStore } from "../../lib/api";
import { clearResourceCache } from "../../lib/cache";
import { setEventsClient } from "../../lib/sse";
import { installFakeFetch, type FakeFetch } from "../../test/fakeFetch";
import { flush, render } from "../../test/render";

let ff: FakeFetch;
beforeEach(() => {
  clearResourceCache();
  setEventsClient({ subscribe: () => () => {}, status: () => ({ connected: true, since: 0 }), close() {} });
  ff = installFakeFetch({
    "GET /api/settings/watchlist": { tickers: ["005930"] },
    "PATCH /api/settings/watchlist": { tickers: ["005930", "AAPL"] },
  });
});
afterEach(() => ff.restore());
const button = (root: ParentNode, text: string) => Array.from(root.querySelectorAll("button")).find((b) => b.textContent === text)!;
const patches = () => ff.calls.filter((c) => c.init?.method === "PATCH");
function add(root: HTMLElement, value: string) {
  const input = root.querySelector("input")!;
  act(() => {
    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")!.set!.call(input, value);
    input.dispatchEvent(new Event("input", { bubbles: true }));
  });
  act(() => button(root, "추가").click());
}

test("인증 없이도 초안은 편집하며 저장은 안내 링크를 제공하고 인증 뒤 확인을 요구한다", async () => {
  const { container } = render(<Watchlist />); await flush();
  expect(container.querySelector('a[href="/system/admin#token"]')).not.toBeNull();
  add(container, "aapl");
  expect(container.querySelector('[data-draft="true"]')?.textContent).toContain("AAPL");
  expect(button(container, "저장").disabled).toBe(true);
  act(() => button(container, "저장").click());
  expect(document.querySelector('[role="dialog"]')).toBeNull();
  expect(patches()).toHaveLength(0);
  act(() => tokenStore.set("saved-fixture"));
  expect(button(container, "저장").disabled).toBe(false);
  act(() => button(container, "저장").click());
  const dialog = document.querySelector('[role="dialog"]')!;
  expect(dialog.textContent).toContain("2종목");
  expect(patches()).toHaveLength(0);
  await act(async () => button(dialog, "확인").click()); await flush();
  expect(JSON.parse(String(patches()[0].init?.body))).toEqual({ tickers: ["005930", "AAPL"] });
  expect(container.textContent).not.toContain("saved-fixture");
});

test("감시목록 확인창이 열린 뒤 인증 키를 제거하면 확인을 막고 초안을 보존한다", async () => {
  tokenStore.set("saved-fixture");
  const { container } = render(<Watchlist />); await flush();
  add(container, "AAPL");
  act(() => button(container, "저장").click());
  const dialog = document.querySelector('[role="dialog"]')!;
  act(() => tokenStore.set(""));
  expect(button(dialog, "확인").disabled).toBe(true);
  act(() => button(dialog, "취소").click());
  expect(container.querySelector('[data-draft="true"]')?.textContent).toContain("AAPL");
  expect(patches()).toHaveLength(0);
});
