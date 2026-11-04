import { act } from "react";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";
import { RunJobButton } from "../RunJobButton";
import { installFakeFetch, type FakeFetch } from "../../test/fakeFetch";
import { flush, render } from "../../test/render";
import { tokenStore } from "../../lib/api";

let ff: FakeFetch | null = null;
beforeEach(() => tokenStore.set("tok"));
afterEach(() => {
  ff?.restore();
  ff = null;
});

const queued = (body: Record<string, unknown>) => ({ id: 7, kind: body.kind, params: body.params, status: "queued", label: "x" });
function route() {
  return (_u: URL, init?: RequestInit) => queued(JSON.parse(String(init!.body)));
}
function bodyOf(i = 0): Record<string, unknown> {
  return JSON.parse(String(ff!.calls[i].init!.body));
}
function button(text: string) {
  return Array.from(document.querySelectorAll("button")).find((b) => b.textContent?.includes(text)) as HTMLButtonElement;
}
function typeId(value: string) {
  const input = document.querySelector('[role="dialog"] input') as HTMLInputElement;
  const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")!.set!;
  act(() => {
    setter.call(input, value);
    input.dispatchEvent(new Event("input", { bubbles: true }));
  });
}
function select(el: HTMLSelectElement, value: string) {
  act(() => {
    el.value = value;
    el.dispatchEvent(new Event("change", { bubbles: true }));
  });
}

describe("RunJobButton", () => {
  test("실전 계좌가 있고 trading kind 이면 고른 계좌 id 를 재입력해야 확인이 켜지고 그 값이 confirm 으로 POST /jobs 에 실린다", async () => {
    ff = installFakeFetch({ "POST /api/jobs": route() });
    const onDone = vi.fn();
    render(<RunJobButton kind="trade_kr" params={{ dry: false }} label="KR 매매" trading realAccounts={["real-1", "real-2"]} onDone={onDone} />);
    act(() => button("KR 매매").click());
    const sel = document.querySelector('[role="dialog"] select') as HTMLSelectElement;
    expect(Array.from(sel.options).map((o) => o.value)).toEqual(["", "real-1", "real-2"]);
    select(sel, "real-2");
    expect(button("확인").disabled).toBe(true);
    typeId("real-1");
    expect(button("확인").disabled).toBe(true);
    typeId("real-2");
    expect(button("확인").disabled).toBe(false);
    act(() => button("확인").click());
    await flush();
    expect(ff.calls).toHaveLength(1);
    expect(ff.calls[0].init!.method).toBe("POST");
    expect((ff.calls[0].init!.headers as Record<string, string>).Authorization).toBe("Bearer tok");
    expect(bodyOf()).toEqual({ kind: "trade_kr", params: { dry: false }, confirm: "real-2" });
    expect(onDone).toHaveBeenCalledWith(7);
    expect(document.querySelector('[role="dialog"]')).toBeNull();
  });

  test("대상 없음 + trading 이면 계좌를 고르기 전에는 확인이 꺼져 있고 POST 하지 않는다", async () => {
    ff = installFakeFetch({ "POST /api/jobs": route() });
    render(<RunJobButton kind="trade_kr" trading realAccounts={["real-1", "real-2"]} />);
    act(() => button("trade_kr").click());
    const sel = document.querySelector('[role="dialog"] select') as HTMLSelectElement;
    expect(sel.value).toBe("");
    const ok = button("확인");
    expect(ok.disabled).toBe(true);
    act(() => ok.click());
    await flush();
    expect(ff.calls).toHaveLength(0);
    expect(document.querySelector('[role="dialog"]')).not.toBeNull();
    select(sel, "real-1");
    expect(button("확인").disabled).toBe(true);
    typeId("real-1");
    expect(button("확인").disabled).toBe(false);
  });

  test("대상 계좌가 실전이면 그 계좌 하나만 미리 선택된 드롭다운 + id 재입력", async () => {
    ff = installFakeFetch({ "POST /api/jobs": route() });
    render(<RunJobButton kind="trade_kr" trading targetAccountId="real-2" realAccounts={["real-1", "real-2"]} />);
    act(() => button("trade_kr").click());
    const sel = document.querySelector('[role="dialog"] select') as HTMLSelectElement;
    expect(Array.from(sel.options).map((o) => o.value)).toEqual(["real-2"]);
    expect(sel.value).toBe("real-2");
    expect(button("확인").disabled).toBe(true);
    typeId("real-2");
    act(() => button("확인").click());
    await flush();
    expect(bodyOf().confirm).toBe("real-2");
  });

  test("모의 대상이면 confirm 없이 POST", async () => {
    ff = installFakeFetch({ "POST /api/jobs": route() });
    render(<RunJobButton kind="trade_kr" trading targetAccountId="paper-1" realAccounts={["real-1"]} />);
    act(() => button("trade_kr").click());
    expect(document.querySelector('[role="dialog"] select')).toBeNull();
    expect(document.querySelector('[role="dialog"] input')).toBeNull();
    act(() => button("확인").click());
    await flush();
    const body = bodyOf();
    expect("confirm" in body).toBe(false);
    expect(body.kind).toBe("trade_kr");
  });

  test("trading 이 아니면 실전 계좌가 있어도 confirm 없이 POST", async () => {
    ff = installFakeFetch({ "POST /api/jobs": route() });
    render(<RunJobButton kind="collect" trading={false} realAccounts={["real-1"]} />);
    act(() => button("collect").click());
    expect(document.querySelector('[role="dialog"] select')).toBeNull();
    act(() => button("확인").click());
    await flush();
    expect("confirm" in bodyOf()).toBe(false);
  });

  test("409 는 버튼 아래 한 줄 오류", async () => {
    ff = installFakeFetch({
      "POST /api/jobs": () => new Response(JSON.stringify({ detail: "데몬이 실행 중이 아닙니다" }), { status: 409 }),
    });
    const onDone = vi.fn();
    const { container } = render(<RunJobButton kind="collect" trading={false} realAccounts={[]} onDone={onDone} />);
    act(() => button("collect").click());
    act(() => button("확인").click());
    await flush();
    expect(container.querySelector(".run-job-error")!.textContent).toBe("데몬이 실행 중이 아닙니다");
    expect(onDone).not.toHaveBeenCalled();
  });

  test("취소하면 POST 하지 않는다", () => {
    ff = installFakeFetch({ "POST /api/jobs": route() });
    render(<RunJobButton kind="collect" trading={false} realAccounts={[]} />);
    act(() => button("collect").click());
    act(() => button("취소").click());
    expect(ff.calls).toHaveLength(0);
    expect(document.querySelector('[role="dialog"]')).toBeNull();
  });
});
