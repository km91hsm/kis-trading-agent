import { act } from "react";
import { expect, test } from "vitest";
import { RunJobButton } from "../RunJobButton";
import { onTokenChange } from "../../lib/api";
import { render } from "../../test/render";

const TOKEN_KEY = "trader.webToken";
const storageChanged = (key: string | null, storageArea = localStorage) => window.dispatchEvent(new StorageEvent("storage", { key, storageArea }));
const button = (root: ParentNode, text: string) => Array.from(root.querySelectorAll("button")).find((node) => node.textContent?.includes(text))!;
const view = () => render(<RunJobButton kind="합성 작업" trading={false} realAccounts={[]} />);

test("다른 탭의 인증 키 저장 신호가 실제 저장소를 다시 읽어 요청 버튼을 활성화한다", () => {
  const { container } = view();
  expect(button(container, "합성 작업").disabled).toBe(true);
  act(() => {
    localStorage.setItem(TOKEN_KEY, "synthetic-other-tab-key");
    storageChanged(TOKEN_KEY);
  });
  expect(button(container, "합성 작업").disabled).toBe(false);
  expect(container.querySelector('a[href="/system/admin#token"]')).toBeNull();
  expect(container.textContent).not.toContain("synthetic-other-tab-key");
});

test.each(["remove", "clear"])("다른 탭의 %s는 버튼과 열린 확인창을 비활성화한다", (operation) => {
  localStorage.setItem(TOKEN_KEY, "synthetic-existing-key");
  const { container } = view();
  act(() => button(container, "합성 작업").click());
  const dialog = document.querySelector('[role="dialog"]')!;
  expect(button(dialog, "확인").disabled).toBe(false);
  act(() => {
    if (operation === "clear") localStorage.clear();
    else localStorage.removeItem(TOKEN_KEY);
    storageChanged(operation === "clear" ? null : TOKEN_KEY);
  });
  expect(button(container, "합성 작업").disabled).toBe(true);
  expect(button(dialog, "확인").disabled).toBe(true);
  expect(container.querySelector('a[href="/system/admin#token"]')).not.toBeNull();
  act(() => button(dialog, "취소").click());
});

test("다른 키나 sessionStorage 신호는 인증 저장소 갱신으로 처리하지 않는다", () => {
  const { container } = view();
  act(() => {
    localStorage.setItem(TOKEN_KEY, "synthetic-unsignaled-key");
    storageChanged("trader.theme");
    storageChanged(TOKEN_KEY, sessionStorage);
    storageChanged(null, sessionStorage);
  });
  expect(button(container, "합성 작업").disabled).toBe(true);
  act(() => storageChanged(TOKEN_KEY));
  expect(button(container, "합성 작업").disabled).toBe(false);
});

test("다른 탭 신호는 인증 키 값을 전달하지 않고 구독 해제 후에는 호출하지 않는다", () => {
  const signals: unknown[][] = [];
  const off = onTokenChange((...args) => signals.push(args));
  window.dispatchEvent(new StorageEvent("storage", {
    key: TOKEN_KEY, storageArea: localStorage,
    oldValue: "synthetic-old-key", newValue: "synthetic-new-key",
  }));
  expect(signals).toEqual([[]]);
  off();
  storageChanged(TOKEN_KEY);
  storageChanged(null);
  expect(signals).toEqual([[]]);
});
