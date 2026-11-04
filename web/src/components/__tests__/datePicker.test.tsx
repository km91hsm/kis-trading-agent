import { act } from "react";
import { afterEach, describe, expect, test, vi } from "vitest";
import { DatePicker } from "../DatePicker";
import { render } from "../../test/render";
import { kstDate } from "../../lib/format";

afterEach(() => vi.useRealTimers());

describe("DatePicker", () => {
  test("'오늘' 은 kstDate(now) 를 onChange 한다", () => {
    vi.useFakeTimers();
    const now = new Date("2026-11-05T16:30:00Z"); // KST 로는 11-06
    vi.setSystemTime(now);
    const onChange = vi.fn();
    const { container } = render(<DatePicker value="2026-11-01" onChange={onChange} max="2026-11-06" />);
    const btn = Array.from(container.querySelectorAll("button")).find((b) => b.textContent === "오늘")!;
    act(() => btn.click());
    expect(onChange).toHaveBeenCalledWith(kstDate(now));
    expect(onChange).toHaveBeenCalledWith("2026-11-06");
  });
  test("입력란은 type=date·value·max 를 갖고 바꾸면 onChange", () => {
    const onChange = vi.fn();
    const { container } = render(<DatePicker value="2026-11-01" onChange={onChange} max="2026-11-05" />);
    const input = container.querySelector('input[type="date"]') as HTMLInputElement;
    expect(input.value).toBe("2026-11-01");
    expect(input.max).toBe("2026-11-05");
    const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")!.set!;
    act(() => {
      setter.call(input, "2026-11-03");
      input.dispatchEvent(new Event("input", { bubbles: true }));
    });
    expect(onChange).toHaveBeenCalledWith("2026-11-03");
  });
});
