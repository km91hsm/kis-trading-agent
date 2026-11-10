import { act } from "react";
import { afterEach, beforeEach, expect, test } from "vitest";
import { Secrets } from "../system/Secrets";
import { tokenStore } from "../../lib/api";
import { installFakeFetch, type FakeFetch } from "../../test/fakeFetch";
import { flush, render } from "../../test/render";

const DATA = { keys: [
  { name: "KIS_TEST_APP_KEY", group: "계좌 전용 · test-account (모의)", label: "앱키", account: "test-account", set: true, env: false },
  { name: "KIS_TEST_APP_SECRET", group: "계좌 전용 · test-account (모의)", label: "앱시크릿", account: "test-account", set: false, env: false },
  { name: "TELEGRAM_BOT_TOKEN", group: "알림", label: "텔레그램 봇 토큰", account: null, set: true, env: true },
], accounts: [] };
const SAVED = { updated: ["KIS_TEST_APP_SECRET"], cleared: [], backup: null, env_overridden: [], restart_required: true };
let ff: FakeFetch;
beforeEach(() => { localStorage.clear(); tokenStore.set("synthetic-token"); });
afterEach(() => { ff?.restore(); localStorage.clear(); });
const button = (root: ParentNode, text: string) => Array.from(root.querySelectorAll("button")).find((b) => b.textContent === text)!;
const toggle = (root: HTMLElement) => act(() => (root.querySelector("button[aria-expanded]") as HTMLButtonElement).click());
function type(root: HTMLElement, name: string, value: string) {
  const input = root.querySelector(`input[name="${name}"]`) as HTMLInputElement;
  act(() => {
    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")!.set!.call(input, value);
    input.dispatchEvent(new Event("input", { bubbles: true }));
  });
}
async function mount(post: unknown = SAVED, get: unknown = DATA) {
  ff = installFakeFetch({ "GET /api/settings/secrets": get, "POST /api/settings/secrets": post });
  const out = render(<Secrets tokenKey={0} />); await flush(); return out;
}
const posts = () => ff.calls.filter((c) => c.init?.method === "POST");

test("기본은 요약만 보이며 펼치면 용도·계좌별 이름, 상태, 입력을 구분한다", async () => {
  const { container } = await mount();
  expect(container.querySelector("form")).toBeNull();
  expect(container.textContent).toContain("설정됨 2");
  expect(container.textContent).toContain("비어 있음 1");
  toggle(container);
  const groups = container.querySelectorAll("fieldset");
  expect(groups).toHaveLength(2);
  expect(groups[0].querySelector("legend")?.textContent).toContain("test-account (모의)");
  expect(groups[0].querySelectorAll("input")).toHaveLength(2);
  expect(groups[1].querySelector("legend")?.textContent).toContain("알림");
  expect(groups[1].textContent).toContain("환경변수 우선");
  expect(Array.from(container.querySelectorAll("input")).every((input) => input.type === "password" && input.labels?.length)).toBe(true);
  expect(button(container, "변경 내용 저장").disabled).toBe(true);
});

test("숨기기는 미저장 입력만 비우며 서버의 키를 수정하지 않는다", async () => {
  const { container } = await mount(); toggle(container);
  type(container, "KIS_TEST_APP_SECRET", "not-saved"); toggle(container);
  expect(container.querySelector("form")).toBeNull();
  expect(posts()).toHaveLength(0);
  toggle(container);
  expect((container.querySelector('input[name="KIS_TEST_APP_SECRET"]') as HTMLInputElement).value).toBe("");
  expect(container.textContent).not.toContain("not-saved");
});

test("확인 뒤 입력한 항목만 저장하며 성공하면 접히고 알림과 포커스를 남긴다", async () => {
  const { container } = await mount(); toggle(container);
  type(container, "KIS_TEST_APP_KEY", "   ");
  type(container, "KIS_TEST_APP_SECRET", "synthetic-replacement");
  act(() => button(container, "변경 내용 저장").click());
  expect(posts()).toHaveLength(0);
  const dialog = document.querySelector('[role="dialog"]')!;
  expect(dialog.textContent).toContain("KIS_TEST_APP_SECRET");
  expect(dialog.textContent).not.toContain("synthetic-replacement");
  await act(async () => button(dialog, "확인").click()); await flush();
  expect(JSON.parse(String(posts()[0].init?.body))).toEqual({ values: { KIS_TEST_APP_SECRET: "synthetic-replacement" } });
  expect(container.querySelector("form")).toBeNull();
  expect(container.textContent).toContain("재시작");
  expect(document.activeElement).toBe(button(container, "연결 키 설정 열기"));
});

test("저장 실패는 펼친 입력을 유지하고 오류를 보여준다", async () => {
  const { container } = await mount(new Response(JSON.stringify({ detail: "synthetic failure" }), { status: 500 }));
  toggle(container); type(container, "KIS_TEST_APP_SECRET", "retry-draft");
  act(() => button(container, "변경 내용 저장").click());
  await act(async () => button(document.querySelector('[role="dialog"]')!, "확인").click()); await flush();
  expect(container.querySelector('[role="alert"]')?.textContent).toContain("synthetic failure");
  expect((container.querySelector('input[name="KIS_TEST_APP_SECRET"]') as HTMLInputElement).value).toBe("retry-draft");
  expect(container.querySelector('button[aria-expanded="true"]')).not.toBeNull();
});

test("토큰이 없거나 조회가 실패해도 연결 키 영역을 열고 숨길 수 있다", async () => {
  localStorage.clear();
  const first = await mount(); toggle(first.container);
  expect(first.container.textContent).toContain("설정 변경 인증을 먼저 설정");
  expect(ff.calls).toHaveLength(0); toggle(first.container); first.unmount(); ff.restore();
  tokenStore.set("synthetic-token");
  const second = await mount(SAVED, new Response(JSON.stringify({ detail: "remote" }), { status: 403 }));
  toggle(second.container);
  expect(second.container.textContent).toContain("이 PC 에서만");
  toggle(second.container);
  expect(second.container.querySelector('button[aria-expanded="false"]')).not.toBeNull();
});
