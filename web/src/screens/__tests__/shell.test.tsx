import { act } from "react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";
import App from "../../app/App";
import { Icon, ICON_NAMES } from "../../components/Icon";
import { NAV } from "../../generated/nav";
import { clearResourceCache } from "../../lib/cache";
import { noteServerTime } from "../../lib/clock";
import { setEventsClient, type EventsClient } from "../../lib/sse";
import { installFakeFetch, type FakeFetch } from "../../test/fakeFetch";
import { fixtureHealth, fixtureUsage } from "../../test/fixtures";
import { flush, render } from "../../test/render";

let fake: FakeFetch | null = null;

const stubClient: EventsClient = { subscribe: () => () => {}, status: () => ({ connected: true, since: 0 }), close() {} };

function app(path = "/") {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <App />
    </MemoryRouter>,
  );
}

const ok = { "GET /api/system/health": fixtureHealth(), "GET /api/system/usage": fixtureUsage() };

beforeEach(() => {
  clearResourceCache();
  setEventsClient(stubClient);
});
afterEach(() => {
  fake?.restore();
  fake = null;
  noteServerTime(new Date(0).toISOString(), 0);
  vi.useRealTimers();
});

test("상태 띠는 health level 과 첫 사유, 사용률 칩 2개를 그린다", async () => {
  fake = installFakeFetch({
    "GET /api/system/health": { level: "warn", reasons: [{ area: "runs", level: "warn", text: "news_collect 연속 3회 오류" }], checked_at: "2026-11-05T10:00:00+09:00" },
    "GET /api/system/usage": fixtureUsage({ five_h_pct: 72, seven_d_pct: 31 }),
  });
  const { container } = app();
  await flush();
  expect(container.querySelector("[data-health]")?.getAttribute("data-health")).toBe("warn");
  expect(container.textContent).toContain("news_collect 연속 3회 오류");
  const chips = container.querySelectorAll("[data-usage-chip]");
  expect(chips).toHaveLength(2);
  expect(chips[0].textContent).toContain("72%");
  expect(chips[0].getAttribute("data-level")).toBe("warn");
  expect(chips[1].textContent).toContain("31%");
});

test("PC 폭에서 왼쪽 메뉴가 하위 목차를 한 줄씩 펼친다 (data-nav-item 수 = NAV items 합)", async () => {
  fake = installFakeFetch(ok);
  const { container } = app();
  await flush();
  const total = Object.values(NAV).reduce((n, s) => n + s.items.length, 0);
  const items = container.querySelectorAll("[data-nav-item]");
  expect(items).toHaveLength(total);
  const first = container.querySelector('[data-nav-item="approvals"]') as HTMLAnchorElement;
  expect(first.getAttribute("href")).toBe("/#approvals");
  expect(container.querySelector('[data-nav-item="regime"]')?.getAttribute("href")).toBe("/system#regime");
  // 오늘 화면의 섹션 컨테이너가 id 로 달려 있다(앵커 계약)
  for (const it of NAV.today.items) expect(container.querySelector(`#${it.id}`)).not.toBeNull();
});

test("AuthError 가 나면 '쓰기 토큰' 안내가 뜨고 닫을 수 있다", async () => {
  fake = installFakeFetch({
    "GET /api/system/health": () => new Response(JSON.stringify({ detail: "토큰" }), { status: 401 }),
    "GET /api/system/usage": fixtureUsage(),
  });
  const { container } = app();
  await flush();
  const notice = container.querySelector("[data-auth-notice]");
  expect(notice?.textContent).toContain("쓰기 토큰이 필요합니다");
  expect(notice?.querySelector("a")?.getAttribute("href")).toBe("/system");
  act(() => (notice!.querySelector("button") as HTMLButtonElement).click());
  expect(container.querySelector("[data-auth-notice]")).toBeNull();
});

test("모르는 경로는 / 로 돌려보내고, 깊은 경로는 그 화면 메뉴를 현재로 표시한다", async () => {
  fake = installFakeFetch(ok);
  const a = app("/nope/where");
  await flush();
  expect(a.container.querySelector('[data-screen="today"]')).not.toBeNull();
  a.unmount();
  const b = app("/accounts/paper-1");
  await flush();
  expect(b.container.querySelector('[data-screen="accounts"]')).not.toBeNull();
  expect(b.container.querySelector('[data-nav-screen="accounts"]')?.getAttribute("aria-current")).toBe("page");
});

test("「N초 전 갱신」과 시계 어긋남을 띠에 쓰고, 60초마다 health 를 다시 읽는다", async () => {
  vi.useFakeTimers({ now: new Date("2026-11-05T10:00:30+09:00") });
  // 서버 시각이 5초 앞선다(응답 헤더 X-Server-Time 으로 보정)
  const ahead = (body: unknown) => () =>
    new Response(JSON.stringify(body), { headers: { "X-Server-Time": new Date(Date.now() + 5000).toISOString() } });
  fake = installFakeFetch({ "GET /api/system/health": ahead(fixtureHealth()), "GET /api/system/usage": ahead(fixtureUsage()) });
  const { container } = app();
  await flush();
  const bar = container.querySelector("[data-statusbar]")!;
  await act(async () => {
    await vi.advanceTimersByTimeAsync(1000); // 시계 1초 틱 — 보정된 지금으로 다시 그린다
  });
  expect(bar.textContent).toContain("36초 전 갱신");
  expect(bar.textContent).toContain("시계 어긋남 +5초");
  const healthCalls = () => fake!.calls.filter((c) => c.url.includes("/system/health")).length;
  expect(healthCalls()).toBe(1);
  await act(async () => {
    await vi.advanceTimersByTimeAsync(60000);
  });
  expect(healthCalls()).toBe(2);
});

describe("정리", () => {
  test("언마운트하면 타이머·IntersectionObserver·테마 감시가 모두 풀린다", async () => {
    vi.useFakeTimers();
    const disconnect = vi.fn();
    const observed: Element[] = [];
    const OrigIO = globalThis.IntersectionObserver;
    globalThis.IntersectionObserver = class {
      observe(el: Element) {
        observed.push(el);
      }
      unobserve() {}
      disconnect = disconnect;
      takeRecords() {
        return [];
      }
    } as unknown as typeof IntersectionObserver;
    const removed = vi.fn();
    const added = vi.fn();
    const origMM = window.matchMedia;
    window.matchMedia = ((q: string) => ({ matches: false, media: q, addEventListener: added, removeEventListener: removed })) as unknown as typeof window.matchMedia;
    try {
      fake = installFakeFetch(ok);
      const { unmount } = app();
      await flush();
      expect(observed.map((e) => e.id).sort()).toEqual(NAV.today.items.map((i) => i.id).sort());
      expect(added).toHaveBeenCalled();
      unmount();
      expect(vi.getTimerCount()).toBe(0);
      expect(disconnect).toHaveBeenCalled();
      expect(removed).toHaveBeenCalledTimes(added.mock.calls.length);
    } finally {
      globalThis.IntersectionObserver = OrigIO;
      window.matchMedia = origMM;
    }
  });
});

describe("테마 토글", () => {
  test("시스템 → 라이트 → 다크 → 시스템 순으로 돌고 저장하며 theme-color 를 맞춘다", async () => {
    const light = document.createElement("meta");
    light.name = "theme-color";
    light.media = "(prefers-color-scheme: light)";
    light.content = "LIGHT";
    const dark = document.createElement("meta");
    dark.name = "theme-color";
    dark.media = "(prefers-color-scheme: dark)";
    dark.content = "DARK";
    document.head.append(light, dark);
    try {
      fake = installFakeFetch(ok);
      const { container } = app();
      await flush();
      const btn = container.querySelector("[data-theme-toggle]") as HTMLButtonElement;
      expect(btn.getAttribute("data-theme-toggle")).toBe("system");
      act(() => btn.click());
      expect(localStorage.getItem("trader.theme")).toBe("light");
      expect(document.documentElement.getAttribute("data-theme")).toBe("light");
      expect([light.content, dark.content]).toEqual(["LIGHT", "LIGHT"]);
      act(() => btn.click());
      expect(localStorage.getItem("trader.theme")).toBe("dark");
      expect(document.documentElement.getAttribute("data-theme")).toBe("dark");
      expect([light.content, dark.content]).toEqual(["DARK", "DARK"]);
      act(() => btn.click());
      expect(localStorage.getItem("trader.theme")).toBe("system");
      expect(btn.getAttribute("data-theme-toggle")).toBe("system");
    } finally {
      light.remove();
      dark.remove();
    }
  });
});

test("아이콘 14종은 currentColor 인라인 SVG 이고 보조기기에서 숨는다", () => {
  expect(ICON_NAMES).toHaveLength(14);
  const { container } = render(<>{ICON_NAMES.map((n) => <Icon key={n} name={n} />)}</>);
  const svgs = container.querySelectorAll("svg");
  expect(svgs).toHaveLength(14);
  for (const svg of Array.from(svgs)) {
    expect(svg.getAttribute("aria-hidden")).toBe("true");
    expect(svg.getAttribute("stroke")).toBe("currentColor");
    expect(svg.childElementCount).toBeGreaterThan(0);
  }
});
