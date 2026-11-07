import { act, StrictMode } from "react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";
import { SYSTEM_PAGES, System } from "../system/System.tsx";
import type { DaemonJob } from "../system/system.ts";
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
  fixtureScalp,
  fixtureSecrets,
  fixtureSettings,
  fixtureStatus,
} from "../../test/fixtures/system";
import { fixtureFoLeg } from "../../test/fixtures/today";
import { flush, render } from "../../test/render";

let ff: FakeFetch | null = null;
/** 시스템 화면은 페이지 4개(상태·잡·기능·관리)다 — 테스트는 넷을 한 트리에 쌓아 절 13개를 한 번에 본다. */
const AllPages = () => (
  <>
    {SYSTEM_PAGES.map((p) => (
      <System key={p} page={p} />
    ))}
  </>
);
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

// 절 순서 = generated nav.ts 의 system 항목 순서(지금 볼 것 → 기능 → 관리)
// 페이지 순(상태 → 잡 → 기능 → 관리), 페이지 안은 nav.ts order 순
const SECTIONS = ["health", "daemon", "usage", "jobs", "runs", "regime", "saveticker", "scalp", "fo", "token", "secrets", "settings", "control-log"];

type Routes = Parameters<typeof installFakeFetch>[0];
const forbidden = () => new Response(JSON.stringify({ detail: "이 PC 에서 직접 연 화면에서만 할 수 있습니다" }), { status: 403 });
function routes(over: Routes = {}): Routes {
  return {
    "GET /api/system/usage": fixtureUsage(),
    "POST /api/system/usage/ensure-fresh": { fresh: true, age_s: 10 },
    "PATCH /api/settings/usage-throttle": (_u: URL, init?: RequestInit) => ({ ...(JSON.parse(String(init?.body)) as object), source: "control" }),
    "GET /api/system/health": fixtureHealth({
      version: "0.11.0",
      features: ["spot", "regime"],
      areas: { jobs: "warn", llm: "ok", regime: "info" },
      reasons: [
        { area: "jobs", level: "warn", text: "24시간 안에 오류로 끝난 잡 2건 (최근 features_backfill)", key: "jobs.failed", link: "/system#runs", count: 2 },
        { area: "regime", level: "info", text: "KR 하락(3/3)", key: "regime.bear", link: null },
      ],
    }),
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
        <AllPages />
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
  test("섹션은 데이터 전에 바로 그려지고 본문 목차는 없다", async () => {
    ff = installFakeFetch(routes());
    render(
      <MemoryRouter>
        <AllPages />
      </MemoryRouter>,
    );
    expect(Array.from(document.querySelectorAll("section.screen-section[id]")).map((e) => e.id)).toEqual(SECTIONS);
    // 본문 목차는 없다 — 왼쪽 메뉴(generated nav.ts 의 system 항목)가 절 앵커를 맡는다
    expect(document.querySelector('nav[aria-label="목차"]')).toBeNull();
    await flush();
    await flush();
  });

  test("사용량: 마운트 시 ensure-fresh 1회(StrictMode·토큰 없음), 새로고침이면 한 번 더, 미터 2개(지출 한도는 관측 없으면 숨김)", async () => {
    await mount(routes({ "GET /api/system/usage": fixtureUsage({ five_h_pct: 72 }) }));
    const calls = callsTo("/system/usage/ensure-fresh", "POST");
    expect(calls).toHaveLength(1);
    expect(header(calls[0].init, "Authorization")).toBeUndefined();
    const u = section("usage");
    const meters = u.querySelectorAll("[data-meter]");
    expect(Array.from(meters).map((m) => m.getAttribute("data-meter"))).toEqual(["5h", "7d"]);
    expect(meters[0].querySelector("meter")?.getAttribute("aria-valuenow")).toBe("72");
    expect(meters[0].getAttribute("data-level")).toBe("warn");
    expect(u.textContent).toContain("사용량 조절 켜짐(설정 파일)");
    expect(u.textContent).toContain("완화 80%");
    expect(u.textContent).not.toContain("statusline"); // 출처는 한글 「상태줄」
    expect(u.textContent).toContain("상태줄");
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

  test("사용량: 요약은 Stats(세션·오류 수), 「지금 확인」은 usage_probe 잡 요청, 지출 한도 미터는 값 없으면 숨김", async () => {
    tokenStore.set("tok");
    const u0 = fixtureUsage();
    u0.caps_by_kind = [{ kind: "reflect", used: 1, cap: 2 }, { kind: "analyze", used: 16, cap: null }];
    u0.windows.h5 = { sessions: 3, cost_usd: 1.5, avg_duration_s: 42, errors: 1 };
    await mount(routes({ "GET /api/system/usage": u0 }));
    const u = section("usage");
    // kind 별 상한 표의 잡 종류는 한글(session.<kind>) — 영문 원문 노출 없음
    expect(u.querySelector("table")?.textContent).toContain("복기");
    expect(u.querySelector("table")?.textContent).not.toContain("reflect");
    const stats = u.querySelector("dl.stats") as HTMLElement;
    expect(stats.textContent).toContain("오늘 세션");
    expect(stats.textContent).toContain("0 / 40");
    expect(stats.textContent).toContain("오류 1");
    expect(Array.from(u.querySelectorAll("[data-meter]")).map((m) => m.getAttribute("data-meter"))).toEqual(["5h", "7d"]);
    act(() => button(u, "지금 확인").click());
    const dlg = document.querySelector('[role="dialog"]') as HTMLElement;
    await act(async () => button(dlg, "확인").click());
    await flush();
    expect(bodiesTo("/jobs", "POST")).toEqual([{ kind: "usage_probe", params: {} }]);
  });

  test("헬스: 영역 격자(단계 칩)·사유 표(건·바로가기)·기능 details, /status 원문 덤프는 없다", async () => {
    await mount(routes());
    const h = section("health");
    expect(h.textContent).toContain("0.11.0");
    expect(h.querySelector("pre")).toBeNull();
    const jobs = h.querySelector('[data-stat="jobs"]') as HTMLElement;
    expect(jobs.textContent).toContain("잡");
    expect(jobs.querySelector('.chip[data-tone="warn"]')?.textContent).toBe("주의");
    expect((h.querySelector('[data-stat="llm"]') as HTMLElement).textContent).toContain("이상 없음");
    const rows = h.querySelectorAll("table tbody tr");
    expect(rows).toHaveLength(2);
    expect(rows[0].textContent).toContain("오류로 끝난 잡 2건");
    expect(rows[0].querySelector("a")?.getAttribute("href")).toBe("/system/jobs#runs");
    // 사유 행마다 단계 칩(주의/관심) — 전체 「정상」과 개별 사유의 범위를 구분
    expect(Array.from(rows[0].querySelectorAll(".chip")).map((c) => c.textContent)).toEqual(["잡", "주의"]); // 백엔드 /system#runs → 절이 있는 페이지 주소로
    expect(rows[0].textContent).toContain("2");
    const details = h.querySelector("details") as HTMLDetailsElement;
    expect(details.querySelector("summary")?.textContent).toContain("켜진 기능 2");
    expect(details.textContent).toContain("regime");
    expect(h.textContent).toContain("일시정지 없음".slice(0, 0)); // 상태 절 제목은 사라졌다
    expect(Array.from(h.querySelectorAll("h3")).map((e) => e.textContent)).not.toContain("상태");
  });

  test("헬스 「지금 점검」 → GET /system/health?live=1 로 다시 그린다", async () => {
    await mount(routes());
    const h = section("health");
    const before = callsTo("/system/health").length;
    await act(async () => button(h, "지금 점검").click());
    await flush();
    const live = callsTo("/system/health").slice(before);
    expect(live).toHaveLength(1);
    expect(new URL(live[0].url, "http://localhost").searchParams.get("live")).toBe("1");
  });

  test("데몬: 머리 Stats(pid·시작), 잡 표는 라벨·시장 칩·일정·소요·한글 상태, 종류 열 없음, 내부 틱 잡은 details", async () => {
    const d = { ...fixtureDaemon() } as Omit<ReturnType<typeof fixtureDaemon>, "jobs"> & { jobs: DaemonJob[] };
    d.jobs = [
      ...d.jobs,
      { id: "job_queue_fast", kind: "job_queue_fast", trigger: "interval", schedule_key: null, interval_s: 10, next_at: null, last_start: null, last_end: null, last_status: "skipped", last_error: null },
    ];
    await mount(routes({ "GET /api/system/daemon": d }));
    const s = section("daemon");
    expect(s.querySelector("dl.stats")?.textContent).toContain("4242");
    const heads = Array.from(s.querySelectorAll("table thead th")).map((e) => e.textContent);
    expect(heads).toContain("일정");
    expect(heads).toContain("소요");
    expect(heads).not.toContain("종류");
    const first = s.querySelector("table tbody tr") as HTMLElement;
    expect(first.textContent).toContain("현물 매매"); // kind 라벨(GET /jobs)
    // 잡 칸의 시장 칩(id 접두 kr_) + 상태 칩 「완료」, 일정은 내부 키(spot.KR) 대신 다음 실행 시각
    expect(Array.from(first.querySelectorAll(".chip")).map((c) => c.textContent)).toEqual(["KR", "완료"]);
    expect(first.textContent).not.toContain("spot.KR");
    expect(first.textContent).toContain("10:30");
    expect(s.textContent).not.toContain("spot_trade");
    const det = s.querySelector("details") as HTMLDetailsElement;
    expect(det.querySelector("summary")?.textContent).toContain("내부 잡 1");
    expect(det.textContent).toContain("10초마다");
  });

  test("설정: Stats 격자 + 모델 표, 조작 이력: 표 행", async () => {
    await mount(routes());
    const st = section("settings");
    expect(st.textContent).toContain("Tailscale 호스트");
    // 설정은 세 묶음(AI 사용 한도 · 거래 리스크 · 접속 환경), 역할별 모델은 details 안 표(역할은 한글, role.* 원문 없음)
    expect(Array.from(st.querySelectorAll("h3")).map((e) => e.textContent)).toEqual(["Codex 토론 참여", "AI 사용 한도", "거래 리스크", "접속 환경"]);
    expect(st.querySelector("details summary")?.textContent).toContain("역할별 모델 2");
    const models = st.querySelectorAll("details table tbody tr");
    expect(models).toHaveLength(2);
    expect(models[0].textContent).toContain("sonnet");
    expect(models[0].textContent).toContain("강세 리서처");
    expect(st.textContent).not.toContain("role.");
    expect(callsTo("/system/control-log")[0].url).toContain("limit=500");
    const log = section("control-log").querySelectorAll("table tbody tr");
    expect(log).toHaveLength(2);
    expect(log[0].textContent).toContain("web");
    expect(log[0].textContent).toContain("ohs");
    expect(log[0].textContent).toContain("account_mode:real-1");
    expect(log[0].textContent).toContain("approve → auto");
  });

  test("잡 큐: 취소는 queued 행에만, Confirm 뒤 DELETE /jobs/{id}; 수동 실행은 레인별 버튼 묶음, 인자는 요약, 일정 표는 없다", async () => {
    tokenStore.set("tok");
    await mount(routes());
    const j = section("jobs");
    const cancels = Array.from(j.querySelectorAll("button")).filter((b) => b.textContent?.trim() === "취소");
    expect(cancels).toHaveLength(1);
    act(() => cancels[0].click());
    expect(callsTo("/jobs/8", "DELETE")).toHaveLength(0);
    const dlg = document.querySelector('[role="dialog"]') as HTMLElement;
    await act(async () => button(dlg, "확인").click());
    await flush();
    expect(callsTo("/jobs/8", "DELETE")).toHaveLength(1);
    expect(callsTo("/jobs/7", "DELETE")).toHaveLength(0);
    expect(j.querySelector("table tbody tr")?.textContent).toContain("KR"); // {"market":"KR"} → KR
    expect(j.textContent).not.toContain('{"market"');
    const grid = j.querySelector(".job-grid") as HTMLElement;
    expect(grid).not.toBeNull();
    const groups = Array.from(grid.querySelectorAll("[data-lane]")).map((g) => g.getAttribute("data-lane"));
    expect(groups).toEqual(["data", "spot"]);
    expect(button(grid, "현물 매매")).toBeTruthy();
    expect(button(grid, "지표 스냅샷")).toBeTruthy();
    expect(Array.from(j.querySelectorAll("h3")).map((e) => e.textContent)).not.toContain("일정");
    // 수동 실행(행동)이 요청 기록 위에 온다
    expect(j.querySelector("h3")?.textContent).toBe("수동 실행");
    expect(j.querySelector(".job-grid")!.compareDocumentPosition(j.querySelector("table")!) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(j.textContent).toContain("데몬"); // 일정은 데몬 절로
  });

  test("runs: 열 이름 「실행 번호」, 종류는 /jobs 라벨 없으면 glossary job.* 한글", async () => {
    const runs = [
      ...fixtureRuns(),
      { id: 29, kind: "briefing", market: null, started_at: "2026-11-05T07:20:00+09:00", finished_at: "2026-11-05T07:20:12+09:00", status: "ok", error: null, result_json: null },
      { id: 28, kind: "analyze", market: "KR", started_at: "2026-11-05T07:30:00+09:00", finished_at: "2026-11-05T07:39:00+09:00", status: "ok", error: null, result_json: null },
    ];
    await mount(routes({ "GET /api/runs": runs }));
    const r = section("runs");
    expect(Array.from(r.querySelectorAll("table thead th")).map((e) => e.textContent)).toContain("실행 번호");
    expect(r.textContent).toContain("아침 브리핑");
    expect(r.textContent).not.toContain("briefing");
    // LLM 세션 run(analyze·scan·market)은 session.* 라벨
    expect(r.textContent).toContain("종목 분석");
    expect(r.textContent).not.toContain("analyze");
  });

  test("runs: 날짜 필터·CSV 링크, 행의 run 을 누르면 /calls?run_id= 와 CSV", async () => {
    await mount(routes());
    const r = section("runs");
    const q = new URL(callsTo("/runs")[0].url, "http://localhost").searchParams;
    expect(q.get("date")).toMatch(/^\d{4}-\d{2}-\d{2}$/);
    expect(Array.from(r.querySelectorAll("a")).some((a) => a.getAttribute("href")?.startsWith("/api/runs?") && a.getAttribute("href")?.includes("format=csv"))).toBe(true);
    const link = button(r, "#31");
    expect(link.className).toContain("link-btn");
    expect(r.querySelector("table tbody tr .chip")?.textContent).toBe("완료");
    act(() => link.click());
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
    expect(t.querySelector("form")).toBeNull();
    expect(t.textContent).toContain("설정됨");
    expect(t.textContent).not.toContain("manual-tok");

    act(() => button(t, "토큰 설정 열기").click());
    expect((t.querySelector("input") as HTMLInputElement).value).toBe("");
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
    act(() => button(s, "연결 키 설정 열기").click());
    expect(s.textContent).toContain("설정됨");
    expect(s.textContent).toContain("비어 있음");
    expect(s.textContent).toContain("환경변수 우선");
    const input = s.querySelector('input[name="TELEGRAM_BOT_TOKEN"]') as HTMLInputElement;
    expect(input.type).toBe("password");
    const loads = callsTo("/settings/secrets").length;
    type(input, "secret-value-1");
    await act(async () => button(s, "변경 내용 저장").click());
    await flush();
    // 확인 대화상자 — 키 이름만 보이고 값은 보이지 않는다
    const dlg = document.querySelector('[role="dialog"]') as HTMLElement;
    expect(dlg.textContent).toContain("TELEGRAM_BOT_TOKEN");
    expect(dlg.textContent).not.toContain("secret-value-1");
    expect(bodiesTo("/settings/secrets", "POST")).toEqual([]);
    await act(async () => button(dlg, "확인").click());
    await flush();
    expect(bodiesTo("/settings/secrets", "POST")).toEqual([{ values: { TELEGRAM_BOT_TOKEN: "secret-value-1" } }]);
    expect(s.querySelector("form")).toBeNull();
    act(() => button(s, "연결 키 설정 열기").click());
    expect((s.querySelector('input[name="TELEGRAM_BOT_TOKEN"]') as HTMLInputElement).value).toBe("");
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
      act(() => button(section("secrets"), "연결 키 설정 열기").click());
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
    act(() => button(s, "연결 키 설정 열기").click());
    type(s.querySelector('input[name="TELEGRAM_BOT_TOKEN"]') as HTMLInputElement, "v");
    await act(async () => button(s, "변경 내용 저장").click());
    await flush();
    await act(async () => button(document.querySelector('[role="dialog"]') as HTMLElement, "확인").click());
    await flush();
    expect(s.querySelector('[role="alert"]')?.textContent).toContain("이 PC 에서만");
  });

  test("데이터 수집 「다시 시작」 → Confirm 뒤 POST /system/saveticker/restart, 응답 note 표시; Stats 4칸", async () => {
    tokenStore.set("tok");
    await mount(routes());
    const st = section("saveticker");
    expect(st.querySelector("dl.stats")?.querySelectorAll(".stat")).toHaveLength(4);
    act(() => button(st, "다시 시작").click());
    expect(callsTo("/system/saveticker/restart", "POST")).toHaveLength(0);
    await act(async () => button(document.querySelector('[role="dialog"]') as HTMLElement, "확인").click());
    await flush();
    expect(callsTo("/system/saveticker/restart", "POST")).toHaveLength(1);
    expect(st.textContent).toContain("Chrome 확장 팝업에서 다시 시작하세요");
  });

  test("정상 healthy/collecting 수집은 한글과 정상색으로 표시한다", async () => {
    await mount(routes({ "GET /api/system/saveticker": fixtureSaveticker({ health: "healthy", service: { state: "collecting", at: null, reason: null } }) }));
    const st = section("saveticker");
    expect(st.querySelector("h2")?.textContent).toBe("데이터 수집");
    expect(st.querySelector(".chip")?.textContent).toBe("정상 · 수집 중");
    expect(st.querySelector(".chip")?.getAttribute("data-tone")).toBe("ok");
    expect(st.textContent).not.toContain("collecting");
  });

  test.each([
    ["waiting_chrome", "waiting_chrome", false, "대기"],
    ["auth_required", "blocked", true, "로그인 필요"],
    ["stopped", "fault", false, "중단"],
    ["error", "fault", false, "오류"],
  ])("수집 서비스 %s는 사람이 이해할 상태로 표시한다", async (state, health, blocked, label) => {
    await mount(routes({ "GET /api/system/saveticker": fixtureSaveticker({ health, blocked, service: { state, at: null, reason: null } }) }));
    expect(section("saveticker").querySelector(".chip")?.textContent).toContain(label);
  });

  test("수집 응답·유효 자료·값 변화·0건을 분리하고 기록된 항목만 집계한다", async () => {
    await mount(routes({ "GET /api/system/saveticker": fixtureSaveticker({
      summary: { state: "partial", recorded: 2, normal: 1, partial: 0, empty: 1, delayed: 0, error: 0, waiting: 0, stopped: 0, login_required: 0, unknown: 0, note: null },
      recovery: { state: "none", text: "복구 대기 없음", lanes: [] },
      streams: [
        { id: "news_all", label: "뉴스 · 전체", state: "normal", status: "ok", count: 12, last_attempt_at: "2026-11-05T00:59:30Z", last_response_at: "2026-11-05T00:59:30Z", last_success_at: "2026-11-05T00:59:30Z", last_change_at: "2026-11-05T00:58:00Z", change_state: "unchanged", error: null, recovery: null },
        { id: "market_turnover_live", label: "증시 · 거래대금", state: "empty", status: "partial", count: 0, last_attempt_at: "2026-11-05T00:59:30Z", last_response_at: "2026-11-05T00:59:30Z", last_success_at: "2026-11-05T00:57:00Z", last_change_at: null, change_state: "empty", error: null, recovery: null },
      ],
    }) }));
    const st = section("saveticker");
    expect(st.textContent).toContain("기록된 항목 2개");
    expect(st.textContent).not.toContain("19/20");
    expect(st.textContent).toContain("응답 성공과 값 변화는 다릅니다");
    const heads = Array.from(st.querySelectorAll("table thead th")).map((h) => h.textContent);
    expect(heads).toEqual(["수집 항목", "상태", "최근 시도", "최근 응답", "유효 자료", "값 변화", "최근 건수"]);
    const rows = st.querySelectorAll("tbody tr");
    expect(rows).toHaveLength(2);
    expect(rows[0].textContent).toContain("변화 없음");
    expect(rows[1].textContent).toContain("0건");
    expect(rows[1].textContent).not.toContain("변화 없음");
  });

  test("기능 라우트가 404 면 「기능 없음」", async () => {
    await mount(routes());
    expect(section("scalp").textContent).toContain("기능 없음");
    expect(section("fo").textContent).toContain("기능 없음");
    expect(section("regime").textContent).not.toContain("기능 없음");
  });

  test("저장소: 설정 안 됨이면 칩도 「설정 안 됨」이고 다시 시작은 비활성", async () => {
    await mount(routes({ "GET /api/system/saveticker": fixtureSaveticker({ configured: false, service: null, health: null, last_success_at: null }) }));
    const st = section("saveticker");
    expect(st.querySelector(".chip")?.textContent).toBe("설정 안 됨");
    expect(st.textContent).not.toContain("알 수 없음");
    expect(button(st, "다시 시작").disabled).toBe(true);
  });

  test("파생 절: 계좌마다 /fo/legs?account_id= 다리 표(다리·상태 칩·수량 체결/주문·가격·시각)", async () => {
    const fo = {
      accounts: [{ id: "paper-fo", env: "paper", mode: "auto", paused: false, state: "idle", enabled: true, triggers: [] }],
      context: null,
    };
    await mount(routes({ "GET /api/system/fo": fo, "GET /api/fo/legs": (url: URL) => (url.searchParams.get("account_id") === "paper-fo" ? [fixtureFoLeg()] : []) }));
    const legs = section("fo").querySelector('[data-legs="paper-fo"]') as HTMLElement;
    expect(legs).not.toBeNull();
    const rows = legs.querySelectorAll("tbody tr");
    expect(rows).toHaveLength(1);
    expect(rows[0].textContent).toContain("진입");
    expect(rows[0].textContent).toContain("R0");
    expect(rows[0].querySelector(".chip")?.textContent).toBe("접수");
    expect(rows[0].textContent).toContain("0/2");
    expect(rows[0].textContent).toContain("시장가");
  });

  test("국면·단타·파생: 비율은 퍼센트로, 상태는 한글 칩, 단타 시장은 표", async () => {
    const scalp = { ...fixtureScalp(), enabled: true, markets: { KR: { open: [1, 2], closed: [], halted: false }, US: { open: [], closed: [3], halted: true } } };
    const fo = {
      accounts: [{ id: "paper-fo", env: "paper", mode: "auto", paused: false, state: "idle", enabled: true, triggers: [] }],
      context: { regime: { state: "bear" }, market_view: { stance: "RISK_OFF" } },
    };
    await mount(routes({ "GET /api/system/scalp": scalp, "GET /api/system/fo": fo }));
    const r = section("regime");
    expect(r.textContent).toContain("최대 비중 20%");
    expect(r.textContent).not.toContain("0.2%");
    expect(r.querySelector("table tbody tr .chip")?.textContent).toBe("강세");
    const s = section("scalp");
    const weight = Array.from(s.querySelectorAll("dl.stats .stat")).find((e) => e.querySelector("dt")?.textContent === "최대 비중");
    expect(weight?.querySelector("dd")?.textContent).toBe("5%");
    expect(s.textContent).not.toContain("0.05%");
    const rows = s.querySelectorAll("table tbody tr");
    expect(rows).toHaveLength(2);
    expect(rows[0].textContent).toContain("KR");
    expect(rows[0].textContent).toContain("2");
    expect(rows[1].querySelector(".chip")?.textContent).toBe("중단");
    const f = section("fo");
    expect(f.textContent).toContain("KR 국면"); // 어느 시장의 국면인지
    expect(f.textContent).toContain("하락");
    expect(f.textContent).toContain("위험 회피");
    expect(f.textContent).not.toContain("RISK_OFF");
    expect(f.textContent).not.toContain("bear");
    expect(f.querySelector("table tbody tr")?.textContent).toContain("모의");
  });

  test("페이지마다 제목에 페이지 이름, 페이지 탭 4개; 실행 기록은 30행씩 ?page=N 으로 나눈다", async () => {
    const runs = Array.from({ length: 35 }, (_, i) => ({ id: 100 - i, kind: "spot_trade", market: "KR", started_at: "2026-11-05T09:30:00+09:00", finished_at: "2026-11-05T09:31:00+09:00", status: "ok", error: null, result_json: null }));
    ff = installFakeFetch(routes({ "GET /api/runs": runs }));
    const { container } = render(
      <MemoryRouter initialEntries={["/system/jobs"]}>
        <System page="jobs" />
      </MemoryRouter>,
    );
    await flush();
    await flush();
    expect(container.querySelector("h1")?.textContent).toBe("시스템 · 잡");
    expect(Array.from(container.querySelectorAll(".page-tabs a")).map((e) => e.getAttribute("href"))).toEqual(["/system", "/system/jobs", "/system/features", "/system/admin"]);
    expect(container.querySelector('.page-tabs a[aria-current="page"]')?.textContent).toBe("잡");
    expect(Array.from(container.querySelectorAll("section.screen-section[id]")).map((e) => e.id)).toEqual(["jobs", "runs"]);
    const r = section("runs");
    expect(r.querySelectorAll("table tbody tr")).toHaveLength(30);
    const pager = r.querySelector("nav.pager") as HTMLElement;
    expect(Array.from(pager.querySelectorAll("button[data-page]")).map((b) => b.textContent)).toEqual(["1", "2"]);
    await act(async () => (pager.querySelector('button[data-page="2"]') as HTMLButtonElement).click());
    expect(r.querySelectorAll("table tbody tr")).toHaveLength(5);
    expect(r.querySelector("table tbody tr")?.textContent).toContain("#70");
  });

  test("잡 큐 요청 기록은 30행씩 ?page=N", async () => {
    const r = fixtureRequests();
    r.requests = Array.from({ length: 35 }, (_, i) => ({ ...r.requests[2], id: 200 - i }));
    await mount(routes({ "GET /api/jobs/requests": r }));
    const j = section("jobs");
    expect(j.querySelectorAll("table tbody tr")).toHaveLength(30);
    expect(Array.from(j.querySelectorAll("nav.pager button[data-page]")).map((b) => b.textContent)).toEqual(["1", "2"]);
  });

  test("잡 페이지의 두 표는 쪽 번호를 따로 쓴다(실행 기록 ?page, 요청 기록 ?rpage); 「데몬 ›」는 상태 페이지 링크", async () => {
    const runs = Array.from({ length: 35 }, (_, i) => ({ id: 100 - i, kind: "spot_trade", market: "KR", started_at: "2026-11-05T09:30:00+09:00", finished_at: "2026-11-05T09:31:00+09:00", status: "ok", error: null, result_json: null }));
    const req = fixtureRequests();
    req.requests = Array.from({ length: 35 }, (_, i) => ({ ...req.requests[2], id: 200 - i }));
    ff = installFakeFetch(routes({ "GET /api/runs": runs, "GET /api/jobs/requests": req }));
    render(
      <MemoryRouter initialEntries={["/system/jobs?page=2"]}>
        <System page="jobs" />
      </MemoryRouter>,
    );
    await flush();
    await flush();
    expect(section("runs").querySelectorAll("table tbody tr")).toHaveLength(5);
    expect(section("jobs").querySelectorAll("table tbody tr")).toHaveLength(30);
    const jp = section("jobs").querySelector("nav.pager") as HTMLElement;
    await act(async () => (jp.querySelector('button[data-page="2"]') as HTMLButtonElement).click());
    expect(section("jobs").querySelectorAll("table tbody tr")).toHaveLength(5);
    expect(section("runs").querySelectorAll("table tbody tr")).toHaveLength(5);
    const daemonLink = Array.from(section("jobs").querySelectorAll("a")).find((a) => a.textContent?.includes("데몬"));
    expect(daemonLink?.getAttribute("href")).toBe("/system#daemon");
  });

  test("실행 기록: 날짜·종류를 바꾸면 1쪽으로 돌아간다", async () => {
    const runs = Array.from({ length: 35 }, (_, i) => ({ id: 100 - i, kind: "spot_trade", market: "KR", started_at: "2026-11-05T09:30:00+09:00", finished_at: "2026-11-05T09:31:00+09:00", status: "ok", error: null, result_json: null }));
    ff = installFakeFetch(routes({ "GET /api/runs": runs }));
    render(
      <MemoryRouter initialEntries={["/system/jobs?page=2"]}>
        <System page="jobs" />
      </MemoryRouter>,
    );
    await flush();
    await flush();
    const r = section("runs");
    expect(r.querySelectorAll("table tbody tr")).toHaveLength(5);
    const sel = r.querySelector('select[aria-label="잡 종류"]') as HTMLSelectElement;
    await act(async () => {
      sel.value = "briefing";
      sel.dispatchEvent(new Event("change", { bubbles: true }));
    });
    await flush();
    await flush();
    expect(section("runs").querySelectorAll("table tbody tr")).toHaveLength(30);
  });

  test("저장소 설정 안 됨: 「설정」 칸을 반복하지 않고 서비스·수집 방식·마지막 성공, 단타 꺼짐이면 시장 칩도 「꺼짐」", async () => {
    const scalp = { ...fixtureScalp(), markets: { KR: { open: [], closed: [], halted: false } } };
    await mount(routes({ "GET /api/system/saveticker": fixtureSaveticker({ configured: false, service: null, health: "unknown" }), "GET /api/system/scalp": scalp }));
    const st = section("saveticker");
    expect(Array.from(st.querySelectorAll("dl.stats dt")).map((e) => e.textContent)).toEqual(["상태", "서비스", "수집 방식", "마지막 성공"]);
    expect(st.textContent).not.toContain("설정 안 됨설정 안 됨");
    const sc = section("scalp");
    expect(sc.querySelector("table tbody tr .chip")?.textContent).toBe("꺼짐");
  });
});
