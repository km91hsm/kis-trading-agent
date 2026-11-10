import { act } from "react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { TokenPanel } from "../system/TokenPanel";
import { tokenStore } from "../../lib/api";
import { installFakeFetch, type FakeFetch } from "../../test/fakeFetch";
import { flush, render } from "../../test/render";

let ff: FakeFetch;
beforeEach(() => { localStorage.clear(); ff = installFakeFetch({}); });
afterEach(() => { ff.restore(); localStorage.clear(); });
const toggle = (root: HTMLElement) => act(() => (root.querySelector("button[aria-expanded]") as HTMLButtonElement).click());
function type(root: HTMLElement, value: string) {
  const input = root.querySelector('input[type="password"]') as HTMLInputElement;
  act(() => {
    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")!.set!.call(input, value);
    input.dispatchEvent(new Event("input", { bubbles: true }));
  });
}

test("저장된 토큰은 상태만 보이고 관리 입력은 접힌다; 열고 숨겨도 토큰을 지우지 않는다", () => {
  tokenStore.set("fixture-token");
  const { container } = render(<TokenPanel onChange={() => {}} />);
  expect(container.querySelector("form")).toBeNull();
  expect(container.textContent).toContain("이 브라우저");
  expect(container.textContent).not.toContain("fixture-token");
  toggle(container);
  expect(container.querySelector('input[type="password"]')).not.toBeNull();
  type(container, "not-saved");
  toggle(container);
  expect(container.querySelector("form")).toBeNull();
  expect(tokenStore.get()).toBe("fixture-token");
  toggle(container);
  expect((container.querySelector("input") as HTMLInputElement).value).toBe("");
  expect(ff.calls).toHaveLength(0);
});

test("토큰이 없어도 입력을 숨길 수 있고 다시 열어 저장하면 입력을 지우고 접는다", () => {
  const { container } = render(<TokenPanel onChange={() => {}} />);
  expect(container.querySelector("form")).not.toBeNull();
  toggle(container);
  expect(container.querySelector("form")).toBeNull();
  toggle(container);
  type(container, "replacement-token");
  act(() => container.querySelector("form")!.dispatchEvent(new Event("submit", { bubbles: true, cancelable: true })));
  expect(tokenStore.get()).toBe("replacement-token");
  expect(container.querySelector("form")).toBeNull();
  expect(container.textContent).not.toContain("replacement-token");
});

test("이 PC에서 받기는 기존 토큰을 한 번 저장하고 접는다", async () => {
  ff.restore(); ff = installFakeFetch({ "POST /api/system/bootstrap-token": { token: "local-fixture" } });
  const { container } = render(<TokenPanel onChange={() => {}} />);
  const fetchButton = Array.from(container.querySelectorAll("button")).find((b) => b.textContent === "이 PC 에서 받기")!;
  await act(async () => fetchButton.click()); await flush();
  expect(tokenStore.get()).toBe("local-fixture");
  expect(container.querySelector("form")).toBeNull();
  expect(ff.calls).toHaveLength(1);
});

test("받기 실패는 오류를 보여주고 입력을 유지한다", async () => {
  ff.restore(); ff = installFakeFetch({ "POST /api/system/bootstrap-token": new Response(JSON.stringify({ detail: "이 PC에서만" }), { status: 403 }) });
  const { container } = render(<TokenPanel onChange={() => {}} />);
  type(container, "draft-fixture");
  await act(async () => Array.from(container.querySelectorAll("button")).find((b) => b.textContent === "이 PC 에서 받기")!.click());
  await flush();
  expect(container.querySelector('[role="alert"]')).not.toBeNull();
  expect((container.querySelector("input") as HTMLInputElement).value).toBe("draft-fixture");
  expect(tokenStore.get()).toBeNull();
});

test("브라우저 저장 실패를 성공으로 표시하거나 입력을 숨기지 않는다", () => {
  const { container } = render(<TokenPanel onChange={() => {}} />);
  type(container, "cannot-save");
  const spy = vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => { throw new Error("storage unavailable"); });
  try {
    act(() => container.querySelector("form")!.dispatchEvent(new Event("submit", { bubbles: true, cancelable: true })));
    expect(container.querySelector("form")).not.toBeNull();
    expect(container.querySelector('[role="alert"]')?.textContent).toContain("저장하지 못했습니다");
    expect((container.querySelector("input") as HTMLInputElement).value).toBe("cannot-save");
    expect(tokenStore.get()).toBeNull();
  } finally { spy.mockRestore(); }
});


test("저장 상태와 서버 검증을 구분하고 로컬·원격 주소별 저장 차이를 설명한다", () => {
  tokenStore.set("fixture-key");
  const { container } = render(<TokenPanel onChange={() => {}} />);
  expect(container.textContent).toContain("저장됨");
  expect(container.textContent).toContain("서버 인증 성공을 뜻하지 않습니다");
  toggle(container);
  expect(container.textContent).toContain("localhost");
  expect(container.textContent).toContain("원격 주소");
  expect(container.textContent).toContain("주소별로 따로");
  expect(ff.calls).toHaveLength(0);
});
