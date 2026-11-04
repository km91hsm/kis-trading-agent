import { act, StrictMode } from "react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";
import { System } from "../system/System.tsx";
import { onAuthError, tokenStore } from "../../lib/api";
import { clearResourceCache } from "../../lib/cache";
import { setEventsClient, type EventsClient } from "../../lib/sse";
import { installFakeFetch, type FakeFetch } from "../../test/fakeFetch";
import { fixtureHealth, fixtureUsage } from "../../test/fixtures";
import {
  fixtureAccounts,
  fixtureCalls,
  fixtureControlLog,
  fixtureDaemon,
  fixtureJobs,
  fixtureRegime,
  fixtureRequests,
  fixtureRuns,
  fixtureSaveticker,
  fixtureSecrets,
  fixtureSettings,
  fixtureStatus,
} from "../../test/fixtures/system";
import { flush, render } from "../../test/render";

let ff: FakeFetch | null = null;
const stub: EventsClient = { subscribe: () => () => {}, status: () => ({ connected: true, since: 0 }), close() {} };

beforeEach(() => {
  vi.clearAllMocks();
  clearResourceCache();
  setEventsClient(stub);
});
afterEach(() => {
  ff?.restore();
  ff = null;
});

const SECTIONS = ["usage", "health", "daemon", "jobs", "runs", "settings", "secrets", "token", "control-log", "regime", "saveticker", "scalp", "fo"];

type Routes = Parameters<typeof installFakeFetch>[0];
const forbidden = () => new Response(JSON.stringify({ detail: "이 PC 에서 직접 연 화면에서만 할 수 있습니다" }), { status: 403 });
function routes(over: Routes = {}): Routes {
  return {
    "GET /api/system/usage": fixtureUsage(),
    "POST /api/system/usage/ensure-fresh": { fresh: true, age_s: 10 },
    "PATCH /api/settings/usage-throttle": (_u: URL, init?: RequestInit) => ({ ...(JSON.parse(String(init?.body)) as object), source: "control" }),
    "GET /api/system/health": fixtureHealth({ version: "0.11.0", features: ["spot", "regime"] }),
    "GET /api/system/status": fixtureStatus(),
    "GET /api/system/daemon": fixtureDaemon(),
    "GET /api/jobs": fixtureJobs(),
    "GET /api/jobs/requests": fixtureRequests(),
    "DELETE /api/jobs/8": () => new Response(null, { status: 204 }),
    "GET /api/runs": fixtureRuns(),
    "GET /api/calls": fixtureCalls(),
    "GET /api/settings": fixtureSettings(),
    "GET /api/settings/secrets": fixtureSecrets(),
    "POST /api/settings/secrets": { updated: ["TELEGRAM_BOT_TOKEN"], cleared: [], backup: null, env_overridden: [], restart_required: true },
    "POST /api/system/bootstrap-token": { token: "boot-tok" },
    "GET /api/system/control-log": fixtureControlLog(),
    "GET /api/system/regime": fixtureRegime(),
    "GET /api/system/saveticker": fixtureSaveticker(),
    "POST /api/system/saveticker/restart": { ok: true, requested: { at: "2026-11-05T10:00:00+09:00", actor: "web" }, note: "Chrome 확장 팝업에서 다시 시작하세요" },
    "GET /api/accounts": fixtureAccounts(),
    ...over,
  };
}

async function mount(r: Routes) {
  ff = installFakeFetch(r);
  const out = render(
    <StrictMode>
      <MemoryRouter initialEntries={["/system"]}>
        <System />
      </MemoryRouter>
    </StrictMode>,
  );
  await flush();
  await flush();
  return out;
}
const section = (id: string) => document.getElementById(id) as HTMLElement;
const callsTo = (path: string, method = "GET") =>
  ff!.calls.filter((c) => (c.init?.method ?? "GET").toUpperCase() === method && new URL(c.url, "http://localhost").pathname === `/api${path}`);
const bodiesTo = (path: string, method: string) => callsTo(path, method).map((c) => JSON.parse(String(c.init!.body)) as unknown);
function button(root: ParentNode, text: string) {
  return Array.from(root.querySelectorAll("button")).find((b) => b.textContent?.trim() === text) as HTMLButtonElement;
}
function type(el: HTMLInputElement, value: string) {
  const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")!.set!;
  act(() => {
    setter.call(el, value);
    el.dispatchEvent(new Event("input", { bubbles: true }));
  });
}
const header = (init: RequestInit | undefined, name: string) => (init?.headers as Record<string, string> | undefined)?.[name];

describe("시스템 화면", () => {
  test("섹션·목차는 데이터 전에 바로 그려진다", async () => {
    ff = installFakeFetch(routes());
    render(
      <MemoryRouter>
        <System />
      </MemoryRouter>,
    );
    for (const id of SECTIONS) expect(section(id)).not.toBeNull();
    const toc = document.querySelector('nav[aria-label="목차"]') as HTMLElement;
    expect(Array.from(toc.querySelectorAll("a")).map((a) => a.getAttribute("href"))).toEqual(SECTIONS.map((s) => `#${s}`));
    await flush();
    await flush();
  });

  test("사용량: 마운트 시 ensure-fresh 1회(StrictMode·토큰 없음), 새로고침이면 한 번 더, 미터 3개", async () => {
    await mount(routes({ "GET /api/system/usage": fixtureUsage({ five_h_pct: 72 }) }));
    const calls = callsTo("/system/usage/ensure-fresh", "POST");
    expect(calls).toHaveLength(1);
    expect(header(calls[0].init, "Authorization")).toBeUndefined();
    const u = section("usage");
    const meters = u.querySelectorAll("[data-meter]");
    expect(Array.from(meters).map((m) => m.getAttribute("data-meter"))).toEqual(["5h", "7d", "spend"]);
    expect(meters[0].querySelector("meter")?.getAttribute("aria-valuenow")).toBe("72");
    expect(meters[0].getAttribute("data-level")).toBe("warn");
    expect(u.textContent).toContain("사용량 조절 켜짐(설정 파일)");
    await act(async () => button(u, "새로고침").click());
    await flush();
    expect(callsTo("/system/usage/ensure-fresh", "POST")).toHaveLength(2);
  });

  test("사용량 조절 끄기: Confirm 뒤 PATCH /settings/usage-throttle {enabled:false}", async () => {
    tokenStore.set("tok");
    await mount(routes());
    act(() => button(section("usage"), "조절 끄기").click());
    const dlg = document.querySelector('[role="dialog"]') as HTMLElement;
    await act(async () => button(dlg, "확인").click());
    await flush();
    expect(bodiesTo("/settings/usage-throttle", "PATCH")).toEqual([{ enabled: false }]);
  });

  test("헬스·데몬·설정·조작 이력: 버전·기능·pid·tailscale 수·이력 한 줄", async () => {
    await mount(routes());
    expect(section("health").textContent).toContain("0.11.0");
    expect(section("health").textContent).toContain("regime");
    expect(section("daemon").textContent).toContain("4242");
    expect(section("daemon").textContent).toContain("spot_trade");
    expect(section("settings").textContent).toContain("Tailscale 호스트");
    expect(callsTo("/system/control-log")[0].url).toContain("limit=100");
    expect(section("control-log").textContent).toContain("2026-11-05 10:00 · web · ohs · account_mode:real-1 · approve → auto");
  });

  test("잡 큐: 취소는 queued 행에만, 누르면 DELETE /jobs/{id}; kind 마다 실행 버튼", async () => {
    tokenStore.set("tok");
    await mount(routes());
    const j = section("jobs");
    const cancels = Array.from(j.querySelectorAll("button")).filter((b) => b.textContent?.trim() === "취소");
    expect(cancels).toHaveLength(1);
    await act(async () => cancels[0].click());
    await flush();
    expect(callsTo("/jobs/8", "DELETE")).toHaveLength(1);
    expect(callsTo("/jobs/7", "DELETE")).toHaveLength(0);
    expect(button(j, "현물 매매")).toBeTruthy();
    expect(button(j, "지표 스냅샷")).toBeTruthy();
  });

  test("runs: 날짜 필터·CSV 링크, 행의 run 을 누르면 /calls?run_id= 와 CSV", async () => {
    await mount(routes());
    const r = section("runs");
    const q = new URL(callsTo("/runs")[0].url, "http://localhost").searchParams;
    expect(q.get("date")).toMatch(/^\d{4}-\d{2}-\d{2}$/);
    expect(Array.from(r.querySelectorAll("a")).some((a) => a.getAttribute("href")?.startsWith("/api/runs?") && a.getAttribute("href")?.includes("format=csv"))).toBe(true);
    act(() => button(r, "31").click());
    await flush();
    expect(new URL(callsTo("/calls")[0].url, "http://localhost").searchParams.get("run_id")).toBe("31");
    expect(r.textContent).toContain("0.0123");
    expect(Array.from(r.querySelectorAll("a")).some((a) => a.getAttribute("href") === "/api/calls?run_id=31&format=csv")).toBe(true);
  });

  test("토큰: 붙여 넣어 저장하면 tokenStore 에, 「이 PC 에서 받기」 는 토큰 없이 POST, 403 이면 「이 PC 에서만」", async () => {
    await mount(routes());
    const t = section("token");
    expect(t.textContent).not.toContain("설정됨");
    const input = t.querySelector('input[aria-label="쓰기 토큰"]') as HTMLInputElement;
    type(input, "manual-tok");
    act(() => button(t, "저장").click());
    expect(tokenStore.get()).toBe("manual-tok");
    expect(input.value).toBe("");
    expect(t.textContent).toContain("설정됨");
    expect(t.textContent).not.toContain("manual-tok");

    await act(async () => button(t, "이 PC 에서 받기").click());
    await flush();
    const boot = callsTo("/system/bootstrap-token", "POST");
    expect(boot).toHaveLength(1);
    expect(header(boot[0].init, "Authorization")).toBeUndefined();
    expect(tokenStore.get()).toBe("boot-tok");
    expect(t.textContent).not.toContain("boot-tok");
  });

  test("토큰 받기 403 → 「이 PC 에서만」", async () => {
    await mount(routes({ "POST /api/system/bootstrap-token": forbidden }));
    await act(async () => button(section("token"), "이 PC 에서 받기").click());
    await flush();
    expect(section("token").textContent).toContain("이 PC 에서만");
    expect(tokenStore.get()).toBeNull();
  });

  test("시크릿: 상태 칸, 저장하면 {values} POST 뒤 입력을 비우고 값은 보이지 않는다, 재시작 안내", async () => {
    tokenStore.set("tok");
    await mount(routes());
    const s = section("secrets");
    expect(s.textContent).toContain("설정됨");
    expect(s.textContent).toContain("비어 있음");
    expect(s.textContent).toContain("환경변수 우선");
    const input = s.querySelector('input[aria-label="TELEGRAM_BOT_TOKEN"]') as HTMLInputElement;
    expect(input.type).toBe("password");
    const loads = callsTo("/settings/secrets").length;
    type(input, "secret-value-1");
    await act(async () => button(s, "저장").click());
    await flush();
    expect(bodiesTo("/settings/secrets", "POST")).toEqual([{ values: { TELEGRAM_BOT_TOKEN: "secret-value-1" } }]);
    expect((s.querySelector('input[aria-label="TELEGRAM_BOT_TOKEN"]') as HTMLInputElement).value).toBe("");
    expect(s.textContent).not.toContain("secret-value-1");
    expect(s.textContent).toContain("재시작");
    expect(callsTo("/settings/secrets")).toHaveLength(loads + 1); // 저장 뒤 다시 읽음
  });

  test("시크릿 GET 403(원격) → 인라인 「이 PC 에서만」, 셸 토큰 띠(onAuthError)는 부르지 않는다", async () => {
    tokenStore.set("tok");
    const seen: unknown[] = [];
    const off = onAuthError((e) => seen.push(e));
    try {
      await mount(routes({ "GET /api/settings/secrets": forbidden }));
      expect(section("secrets").textContent).toContain("이 PC 에서만");
      expect(seen).toHaveLength(0);
    } finally {
      off();
    }
  });

  test("시크릿 POST 403 → 「이 PC 에서만」", async () => {
    tokenStore.set("tok");
    await mount(routes({ "POST /api/settings/secrets": forbidden }));
    const s = section("secrets");
    type(s.querySelector('input[aria-label="TELEGRAM_BOT_TOKEN"]') as HTMLInputElement, "v");
    await act(async () => button(s, "저장").click());
    await flush();
    expect(s.querySelector('[role="alert"]')?.textContent).toContain("이 PC 에서만");
  });

  test("저장소 「다시 시작」 → POST /system/saveticker/restart, 응답 note 표시", async () => {
    tokenStore.set("tok");
    await mount(routes());
    const st = section("saveticker");
    await act(async () => button(st, "다시 시작").click());
    await flush();
    expect(callsTo("/system/saveticker/restart", "POST")).toHaveLength(1);
    expect(st.textContent).toContain("Chrome 확장 팝업에서 다시 시작하세요");
  });

  test("기능 라우트가 404 면 「기능 없음」", async () => {
    await mount(routes());
    expect(section("scalp").textContent).toContain("기능 없음");
    expect(section("fo").textContent).toContain("기능 없음");
    expect(section("regime").textContent).not.toContain("기능 없음");
  });
});
