import { act } from "react";
import { describe, expect, test, vi } from "vitest";
import { Confirm } from "../Confirm";
import { render } from "../../test/render";

function dialog() {
  return document.querySelector('[role="dialog"]') as HTMLElement | null;
}
function button(text: string) {
  return Array.from(document.querySelectorAll("button")).find((b) => b.textContent === text) as HTMLButtonElement;
}
function type(input: HTMLInputElement, value: string) {
  const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")!.set!;
  act(() => {
    setter.call(input, value);
    input.dispatchEvent(new Event("input", { bubbles: true }));
  });
}
function key(k: string, shift = false) {
  act(() => {
    (document.activeElement ?? document.body).dispatchEvent(new KeyboardEvent("keydown", { key: k, shiftKey: shift, bubbles: true }));
  });
}

describe("Confirm", () => {
  test("open=false 면 그리지 않는다", () => {
    render(<Confirm open={false} title="t" body="b" onConfirm={() => {}} onClose={() => {}} />);
    expect(dialog()).toBeNull();
  });

  test("live 가 없으면 확인 → onConfirm(undefined)", () => {
    const onConfirm = vi.fn();
    render(<Confirm open title="잡 실행" body="실행할까요?" onConfirm={onConfirm} onClose={() => {}} />);
    const d = dialog()!;
    expect(d.getAttribute("aria-modal")).toBe("true");
    expect(d.textContent).toContain("실행할까요?");
    act(() => button("확인").click());
    expect(onConfirm).toHaveBeenCalledWith(undefined);
  });

  test("live 확인은 계좌 id 를 정확히 입력해야 확인 버튼이 켜진다", () => {
    const onConfirm = vi.fn();
    render(<Confirm open title="실전 전환" body="실전으로 바꿉니다" live={{ accountId: "real-1" }} onConfirm={onConfirm} onClose={() => {}} />);
    const ok = button("확인");
    expect(ok.disabled).toBe(true);
    const input = dialog()!.querySelector("input")!;
    type(input, "real-");
    expect(ok.disabled).toBe(true);
    type(input, "real-1 ");
    expect(ok.disabled).toBe(true);
    type(input, "real-1");
    expect(ok.disabled).toBe(false);
    act(() => ok.click());
    expect(onConfirm).toHaveBeenCalledWith("real-1");
  });

  test("Esc 와 취소는 onClose", () => {
    const onClose = vi.fn();
    render(<Confirm open title="t" body="b" onConfirm={() => {}} onClose={onClose} />);
    key("Escape");
    expect(onClose).toHaveBeenCalledTimes(1);
    act(() => button("취소").click());
    expect(onClose).toHaveBeenCalledTimes(2);
  });

  test("포커스 트랩 — 열리면 대화상자 안으로, Tab 은 안에서 돈다", () => {
    render(<Confirm open title="t" body="b" live={{ accountId: "r" }} onConfirm={() => {}} onClose={() => {}} />);
    const d = dialog()!;
    const input = d.querySelector("input")!;
    expect(document.activeElement).toBe(input);
    type(input, "r"); // 확인 버튼 활성 → 마지막 포커스 대상
    const ok = button("확인");
    act(() => ok.focus());
    key("Tab");
    expect(document.activeElement).toBe(input);
    key("Tab", true);
    expect(document.activeElement).toBe(ok);
  });

  test("닫히면 열기 전 포커스로 돌아간다", () => {
    const opener = document.createElement("button");
    document.body.appendChild(opener);
    opener.focus();
    const r = render(<Confirm open title="t" body="b" onConfirm={() => {}} onClose={() => {}} />);
    expect(dialog()!.contains(document.activeElement)).toBe(true);
    r.rerender(<Confirm open={false} title="t" body="b" onConfirm={() => {}} onClose={() => {}} />);
    expect(document.activeElement).toBe(opener);
    opener.remove();
  });
});
