// 옛 `web/src/theme.test.ts` 이식
import { afterEach, describe, expect, it, vi } from "vitest";
import { applyTheme, resolveTheme, themeStore, watchSystemTheme } from "../theme";

describe("resolveTheme", () => {
  it("system 은 OS 선호를 따른다", () => {
    expect(resolveTheme("system", true)).toBe("dark");
    expect(resolveTheme("system", false)).toBe("light");
  });
  it("명시 값은 OS 선호를 무시한다", () => {
    expect(resolveTheme("light", true)).toBe("light");
    expect(resolveTheme("dark", false)).toBe("dark");
  });
});

describe("themeStore", () => {
  it("기본은 system 이고 set/get 이 왕복한다", () => {
    expect(themeStore.get()).toBe("system");
    themeStore.set("dark");
    expect(themeStore.get()).toBe("dark");
    expect(localStorage.getItem("trader.theme")).toBe("dark");
  });
  it("모르는 값은 system 으로 읽는다", () => {
    localStorage.setItem("trader.theme", "neon");
    expect(themeStore.get()).toBe("system");
  });
});

describe("applyTheme", () => {
  it("기본은 <html data-theme> 에 쓴다", () => {
    applyTheme("dark");
    expect(document.documentElement.dataset.theme).toBe("dark");
    applyTheme("light");
    expect(document.documentElement.dataset.theme).toBe("light");
  });
  it("root 를 주면 그 요소에 쓴다", () => {
    const el = document.createElement("div");
    applyTheme("dark", el);
    expect(el.getAttribute("data-theme")).toBe("dark");
  });
});

describe("watchSystemTheme", () => {
  const original = window.matchMedia;
  afterEach(() => {
    window.matchMedia = original;
  });
  it("OS 선호 변화를 알리고 해제 함수가 구독을 끊는다", () => {
    const listeners = new Set<(e: MediaQueryListEvent) => void>();
    window.matchMedia = vi.fn(() => ({
      matches: false,
      addEventListener: (_: string, l: (e: MediaQueryListEvent) => void) => listeners.add(l),
      removeEventListener: (_: string, l: (e: MediaQueryListEvent) => void) => listeners.delete(l),
    })) as unknown as typeof window.matchMedia;
    const cb = vi.fn();
    const stop = watchSystemTheme(cb);
    listeners.forEach((l) => l({ matches: true } as MediaQueryListEvent));
    expect(cb).toHaveBeenCalledWith(true);
    stop();
    expect(listeners.size).toBe(0);
  });
});
