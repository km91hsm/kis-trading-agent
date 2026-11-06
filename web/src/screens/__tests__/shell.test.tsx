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
import { fixtureAccounts } from "../../test/fixtures/accounts";
import { fixtureAccount } from "../../test/fixtures/today";
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
  expect(chips[1].getAttribute("data-level")).toBe("ok"); // 70% 미만 = 정상(초록)
});

test("PC 폭에서 왼쪽 메뉴는 현재 화면의 하위 목차만 한 줄씩 펼친다 (data-nav-item = 그 화면의 NAV items)", async () => {
  fake = installFakeFetch(ok);
  const { container } = app();
  await flush();
  // 승인·계좌 응답이 404(오류)라 오늘 화면의 섹션 4개가 모두 보인다 → 항목 4개, 다른 화면 항목은 없다
  const items = Array.from(container.querySelectorAll("[data-nav-item]")).map((e) => e.getAttribute("data-nav-item"));
  expect(items).toEqual(NAV.today.items.map((i) => i.id));
  const first = container.querySelector('[data-nav-item="approvals"]') as HTMLAnchorElement;
  expect(first.getAttribute("href")).toBe("/#approvals");
  expect(container.querySelector('[data-nav-item="regime"]')).toBeNull();
  // 오늘 화면의 섹션 컨테이너가 id 로 달려 있다(앵커 계약)
  for (const it of NAV.today.items) expect(container.querySelector(`#${it.id}`)).not.toBeNull();
});

describe("왼쪽 메뉴 2단", () => {
  let origScroll: typeof Element.prototype.scrollIntoView;
  const scroll = vi.fn();
  beforeEach(() => {
    origScroll = Element.prototype.scrollIntoView;
    Element.prototype.scrollIntoView = scroll;
    scroll.mockClear();
  });
  afterEach(() => {
    Element.prototype.scrollIntoView = origScroll;
  });

  test("숨긴 섹션(승인 0건·없는 레인)의 항목은 메뉴에서 뺀다", async () => {
    fake = installFakeFetch({ ...ok, "GET /api/approvals": { approvals: [] }, "GET /api/accounts": [fixtureAccount()] });
    const { container } = app();
    await flush();
    const items = Array.from(container.querySelectorAll("[data-nav-item]")).map((e) => e.getAttribute("data-nav-item"));
    expect(items).toEqual(["spot-lane", "decisions", "orders-timeline", "next-jobs", "notices"]); // 승인·단타·파생은 빠짐
    expect((container.querySelector("#fo-lane") as HTMLElement).hidden).toBe(true);
  });

  test("시스템 화면의 하위 메뉴는 절 앵커가 아니라 페이지 4개(상태·잡·기능·관리)이고, 페이지마다 자기 절만 그린다", async () => {
    fake = installFakeFetch(ok);
    const a = app("/system");
    await flush();
    const items = Array.from(a.container.querySelectorAll("[data-nav-item]")).map((e) => e.getAttribute("data-nav-item"));
    expect(items).toEqual(["status", "jobs", "features", "admin"]);
    const hrefs = Array.from(a.container.querySelectorAll("[data-nav-item]")).map((e) => e.getAttribute("href"));
    expect(hrefs).toEqual(["/system", "/system/jobs", "/system/features", "/system/admin"]);
    expect(a.container.querySelector('[data-nav-item="status"]')?.getAttribute("aria-current")).toBe("page");
    expect(a.container.querySelector("#health")).not.toBeNull();
    expect(a.container.querySelector("#runs")).toBeNull();
    a.unmount();
    const b = app("/system/jobs");
    await flush();
    expect(b.container.querySelector('[data-nav-screen="system"]')?.getAttribute("aria-current")).toBe("page");
    expect(b.container.querySelector('[data-nav-item="jobs"]')?.getAttribute("aria-current")).toBe("page");
    expect(b.container.querySelector('[data-nav-item="status"]')?.getAttribute("aria-current")).toBeNull();
    expect(b.container.querySelector("#runs")).not.toBeNull();
    expect(b.container.querySelector("#health")).toBeNull();
    // 페이지 탭(좁은 폭용)도 4개
    expect(Array.from(b.container.querySelectorAll(".page-tabs a")).map((e) => e.textContent)).toEqual(["상태", "잡", "기능", "관리"]);
  });

  test("하위 목차를 누르면 그 항목이 현재가 되고, 같은 항목을 다시 눌러도 스크롤한다", async () => {
    fake = installFakeFetch(ok);
    const { container } = app("/data");
    await flush();
    const link = container.querySelector('[data-nav-item="themes"]') as HTMLAnchorElement;
    expect(link.getAttribute("aria-current")).toBeNull();
    act(() => link.click());
    expect(link.getAttribute("aria-current")).toBe("location");
    expect(scroll).toHaveBeenCalledTimes(1);
    expect((scroll.mock.instances[0] as Element).id).toBe("themes");
    act(() => link.click());
    expect(scroll).toHaveBeenCalledTimes(2);
    expect(link.getAttribute("aria-current")).toBe("location");
  });

  test("스크롤 입력(wheel)이 오면 눌렀던 항목 대신 보이는 가장 위 섹션을 현재로 표시한다", async () => {
    const OrigIO = globalThis.IntersectionObserver;
    // 관찰을 시작하자마자 모든 섹션이 보인다고 알리는 가짜 — 보이는 가장 위 = 데이터 첫 항목(quotes)
    globalThis.IntersectionObserver = class {
      cb: IntersectionObserverCallback;
      constructor(cb: IntersectionObserverCallback) {
        this.cb = cb;
      }
      observe(el: Element) {
        this.cb([{ target: el, isIntersecting: true } as IntersectionObserverEntry], this as unknown as IntersectionObserver);
      }
      unobserve() {}
      disconnect() {}
      takeRecords() {
        return [];
      }
    } as unknown as typeof IntersectionObserver;
    try {
      fake = installFakeFetch(ok);
      const { container } = app("/data");
      await flush();
      const regime = container.querySelector(`[data-nav-item="${NAV.data.items[0].id}"]`) as HTMLAnchorElement;
      const save = container.querySelector('[data-nav-item="themes"]') as HTMLAnchorElement;
      expect(regime.getAttribute("aria-current")).toBe("location");
      act(() => save.click());
      expect(save.getAttribute("aria-current")).toBe("location");
      expect(regime.getAttribute("aria-current")).toBeNull();
      act(() => {
        window.dispatchEvent(new Event("wheel"));
      });
      expect(regime.getAttribute("aria-current")).toBe("location");
      expect(save.getAttribute("aria-current")).toBeNull();
    } finally {
      globalThis.IntersectionObserver = OrigIO;
    }
  });

  test("메뉴 푸터에 데몬 상태와 버전을 쓰고, health 는 상태 띠와 한 번만 부른다", async () => {
    fake = installFakeFetch({ ...ok, "GET /api/system/health": fixtureHealth({ version: "0.2.0" }) });
    const { container } = app();
    await flush();
    const foot = container.querySelector("[data-nav-foot]");
    expect(foot?.textContent).toContain("데몬 정상");
    expect(foot?.textContent).toContain("v0.2.0");
    expect(fake.calls.filter((c) => c.url.includes("/system/health"))).toHaveLength(1);
  });
});

describe("상세 화면 하위 목차(본문 DOM)", () => {
  test("/accounts/paper-1: 2단은 「‹ 돌아가기」(/accounts) 다음에 본문 section[id] > h2 목차 — 숨긴 절(단타)은 뺀다", async () => {
    fake = installFakeFetch({ ...ok, "GET /api/accounts": fixtureAccounts() }); // 보유·주문은 404 → 단타 절 hidden
    const { container } = app("/accounts/paper-1");
    await flush();
    expect(container.querySelector('[data-nav-screen="accounts"]')?.getAttribute("aria-current")).toBe("page");
    const items = Array.from(container.querySelectorAll("[data-nav-item]"));
    expect(items[0].getAttribute("data-nav-item")).toBe("back");
    expect(items[0].textContent).toBe("‹ 돌아가기");
    expect(items[0].getAttribute("href")).toBe("/accounts");
    const rest = items.slice(1).map((e) => [e.getAttribute("data-nav-item"), e.textContent, e.getAttribute("href")]);
    expect(rest).toEqual([
      ["orders", "오늘 주문·체결", "/accounts/paper-1#orders"],
      ["positions", "보유", "/accounts/paper-1#positions"],
      ["risk", "리스크", "/accounts/paper-1#risk"],
      ["nav-chart", "NAV 추이", "/accounts/paper-1#nav-chart"],
    ]);
  });

  test("/decisions/spot/1: 오늘 메뉴가 현재, 돌아가기는 /, 목차는 h2 가 있는 절만(머리 카드 절은 제외)", async () => {
    const err = () => new Response("{}", { status: 500, headers: { "Content-Type": "application/json" } });
    fake = installFakeFetch({ ...ok, "GET /api/decisions/spot/1": err(), "GET /api/decisions/spot/1/trace": err() }); // 404 면 「결정 없음」 분기
    const { container } = app("/decisions/spot/1");
    await flush();
    expect(container.querySelector('[data-nav-screen="today"]')?.getAttribute("aria-current")).toBe("page");
    const items = Array.from(container.querySelectorAll("[data-nav-item]")).map((e) => e.getAttribute("data-nav-item"));
    expect(items[0]).toBe("back");
    expect(container.querySelector('[data-nav-item="back"]')?.getAttribute("href")).toBe("/");
    expect(items).toContain("rationale");
    expect(items).not.toContain("header");
    expect(container.querySelector('[data-nav-item="rationale"]')?.textContent).toBe("근거 전문");
    expect(container.querySelector('[data-nav-item="rationale"]')?.getAttribute("href")).toBe("/decisions/spot/1#rationale");
  });
});

test("「N초 전 갱신」은 /system#health 링크이고, 테마 버튼 글자는 「테마: …」", async () => {
  fake = installFakeFetch(ok);
  const { container } = app();
  await flush();
  const ago = container.querySelector(".statusbar-ago") as HTMLAnchorElement;
  expect(ago.tagName).toBe("A");
  expect(ago.getAttribute("href")).toBe("/system#health");
  const btn = container.querySelector("[data-theme-toggle]") as HTMLButtonElement;
  expect(btn.textContent).toContain("테마: 시스템");
  expect(btn.textContent).not.toMatch(/^\s*시스템/);
});

describe("지수·환율 칩", () => {
  const indices = (session: "open" | "closed") => ({
    session,
    at: "2026-11-05T10:00:00+09:00",
    indices: session === "open" ? { KOSPI: { price: "2540.12", change_pct: 0.41, at: "2026-11-05T10:00:00+09:00", stale: false, source: "rest" }, KOSDAQ: { price: "812.3", change_pct: -1.2, at: "2026-11-05T10:00:00+09:00", stale: false, source: "rest" } } : {},
    next_poll_ms: 60000,
  });
  const fx = { pair: "USDKRW", rate: "1340.8", as_of: "2026-11-05", source: "kis" };

  test("장중이면 KOSPI·KOSDAQ 값과 등락, 환율 칩", async () => {
    fake = installFakeFetch({ ...ok, "GET /api/quotes/indices": indices("open"), "GET /api/quotes/fx": fx });
    const { container } = app();
    await flush();
    const chips = Array.from(container.querySelectorAll("[data-index-chip]"));
    expect(chips.map((c) => c.getAttribute("data-index-chip"))).toEqual(["KOSPI", "KOSDAQ", "USDKRW"]);
    expect(chips[0].textContent).toContain("2,540.12");
    expect(chips[0].textContent).toContain("▲0.41%");
    expect(chips[0].getAttribute("data-dir")).toBe("up");
    expect(chips[1].textContent).toContain("▼1.20%");
    expect(chips[1].getAttribute("data-dir")).toBe("down");
    expect(chips[2].textContent).toContain("1,340.8");
  });

  test("장 밖이면 지수 칩은 없고, 환율 404 면 환율 칩도 없다", async () => {
    fake = installFakeFetch({ ...ok, "GET /api/quotes/indices": indices("closed"), "GET /api/quotes/fx": () => new Response(JSON.stringify({ detail: "없음" }), { status: 404 }) });
    const { container } = app();
    await flush();
    expect(container.querySelectorAll("[data-index-chip]")).toHaveLength(0);
  });
});

test("좁은 폭(<768)에서는 상단 띠가 한 줄: 사용률 칩은 details 안으로", async () => {
  const orig = window.innerWidth;
  Object.defineProperty(window, "innerWidth", { configurable: true, writable: true, value: 390 });
  try {
    fake = installFakeFetch(ok);
    const { container } = app();
    await flush();
    const more = container.querySelector("details.statusbar-more") as HTMLDetailsElement;
    expect(more).not.toBeNull();
    expect(more.querySelectorAll("[data-usage-chip]")).toHaveLength(2);
    expect(container.querySelector("[data-statusbar] > .usage-chips")).toBeNull();
  } finally {
    Object.defineProperty(window, "innerWidth", { configurable: true, writable: true, value: orig });
  }
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
  expect(notice?.querySelector("a")?.getAttribute("href")).toBe("/system/admin#token"); // 토큰 절로 바로
  expect(notice?.querySelector("a")?.textContent).toBe("시스템 › 관리 › 쓰기 토큰");
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
