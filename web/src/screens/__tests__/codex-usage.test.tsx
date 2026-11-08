import { act } from "react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { UsageChips } from "../../app/shell/UsageChips";
import { clearResourceCache } from "../../lib/cache";
import { setEventsClient, type EventsClient } from "../../lib/sse";
import { installFakeFetch, type FakeFetch } from "../../test/fakeFetch";
import { fixtureUsage } from "../../test/fixtures";
import { flush, render } from "../../test/render";
import { Usage } from "../system/Usage";

let fake: FakeFetch | null = null;
const now = new Date("2026-11-07T10:00:00+09:00");
const events: EventsClient = { subscribe: () => () => {}, status: () => ({ connected: true, since: 0 }), close() {} };
const codex = {
  enabled: false,
  usage: { calls: 4, daily_call_cap: 80, errors: 1, duration_ms: 12000, input_tokens: null, output_tokens: null, cost_usd: null },
  rate_limits: { status: "ready", reason: null, observed_at: now.toISOString(), age_s: 0, five_hour: null, seven_day: { used_pct: 89, resets_at: "2026-11-08T10:00:00+09:00" } },
};
const routes = () => ({
  "GET /api/system/usage": fixtureUsage({ five_h_pct: 72, seven_d_pct: 31 }),
  "GET /api/system/codex-usage": codex,
  "POST /api/system/usage/ensure-fresh": { fresh: true },
});
beforeEach(() => { clearResourceCache(); setEventsClient(events); });
afterEach(() => { fake?.restore(); fake = null; vi.useRealTimers(); });

test("상단은 토론이 꺼져도 Claude와 Codex의 실제 시간 창을 함께 표시한다", async () => {
  fake = installFakeFetch(routes());
  const { container } = render(<UsageChips now={now} />);
  await flush();
  expect(container.textContent).toContain("Claude");
  expect(container.textContent).toContain("Codex");
  const chips = container.querySelector('[aria-label="Codex 사용률"]');
  expect(chips?.textContent).toContain("7일");
  expect(chips?.textContent).toContain("89%");
  expect(chips?.textContent).toContain("5시간");
  expect(chips?.textContent).toContain("미제공");
});

test("사용량 화면에 구독 한도와 프로그램 호출 기록을 구분하고 미제공 토큰·비용은 0으로 표시하지 않는다", async () => {
  fake = installFakeFetch(routes());
  const { container } = render(<Usage />);
  await flush();
  const section = container.querySelector('[aria-label="Codex 사용량"]');
  expect(section?.textContent ?? "").toContain("토론 참여 꺼짐");
  expect(section?.textContent).toContain("89%");
  expect(section?.textContent).toContain("4 / 80회");
  expect(section?.textContent).toContain("프로그램 기록");
  expect(section?.textContent).toContain("미제공");
  expect(section?.textContent).not.toContain("$0.00");
});

test("구독 조회 실패는 이유와 확인 불가를 표시하고 프로그램 기록을 유지한다", async () => {
  fake = installFakeFetch({ ...routes(), "GET /api/system/codex-usage": { ...codex, rate_limits: { status: "unavailable", reason: "authentication_required", observed_at: null, age_s: null, five_hour: null, seven_day: null } } });
  const { container } = render(<Usage />);
  await flush();
  const section = container.querySelector('[aria-label="Codex 사용량"]');
  expect(section?.textContent ?? "").toContain("로그인");
  expect(section?.textContent).toContain("확인 불가");
  expect(section?.textContent).toContain("4 / 80회");
  expect(section?.querySelector("meter")).toBeNull();
});

test("다른 Codex 앱에서 늘어난 구독 사용량도 1분 뒤 갱신한다", async () => {
  vi.useFakeTimers();
  let percent = 89;
  fake = installFakeFetch({ ...routes(), "GET /api/system/codex-usage": () => ({ ...codex, rate_limits: { ...codex.rate_limits, seven_day: { used_pct: percent, resets_at: null } } }) });
  const { container } = render(<UsageChips now={now} />);
  await flush();
  percent = 92;
  await act(async () => { await vi.advanceTimersByTimeAsync(60000); });
  await flush();
  expect(container.querySelector('[aria-label="Codex 사용률"]')?.textContent ?? "").toContain("92%");
});

test("갱신 실패로 남은 지난 관측은 상단과 상세에 이전 관측으로 표시한다", async () => {
  fake = installFakeFetch({ ...routes(), "GET /api/system/codex-usage": { ...codex, rate_limits: { ...codex.rate_limits, status: "stale", reason: "timeout", age_s: 120 } } });
  const { container } = render(<><UsageChips now={now} /><Usage /></>);
  await flush();
  expect(container.querySelector('[aria-label="Codex 사용률"]')?.textContent ?? "").toContain("이전 관측");
  expect(container.querySelector('[aria-label="Codex 사용량"]')?.textContent ?? "").toContain("이전 관측");
});

test("HTTP 갱신 실패 후 남아 있는 값도 이전 관측임을 표시한다", async () => {
  vi.useFakeTimers();
  let fail = false;
  fake = installFakeFetch({ ...routes(), "GET /api/system/codex-usage": () => fail ? new Response("{}", { status: 503 }) : codex });
  const { container } = render(<><UsageChips now={now} /><Usage /></>);
  await flush();
  fail = true;
  await act(async () => { await vi.advanceTimersByTimeAsync(60000); });
  await flush();
  expect(container.querySelector('[aria-label="Codex 사용률"]')?.textContent ?? "").toContain("이전 관측");
  const section = container.querySelector('[aria-label="Codex 사용량"]');
  expect(section?.textContent ?? "").toContain("확인 불가");
  expect(section?.textContent ?? "").toContain("이전 프로그램 기록");
  expect(section?.textContent ?? "").toContain("4 / 80회");
});

// --- 하루 제한·Codex 사용량 조절 설정 (2026-11-08) ---
import { tokenStore } from "../../lib/api";

const buttonIn = (root: ParentNode, text: string) => Array.from(root.querySelectorAll("button")).find((b) => b.textContent === text) as HTMLButtonElement | undefined;
const typeInto = (input: HTMLInputElement, value: string) => act(() => {
  Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")!.set!.call(input, value);
  input.dispatchEvent(new Event("input", { bubbles: true }));
});
const withThrottle = (over: object = {}) => ({ ...codex, usage: { ...codex.usage, cap_source: "yaml" }, throttle: { enabled: true, source: "yaml", hard_pct: 0.95, state: "ok", reason: null, ...over } });
const settingsRoutes = (codexResp: object = withThrottle()) => ({
  ...routes(),
  "GET /api/system/codex-usage": codexResp,
  "PATCH /api/settings/session-cap": (_u: URL, init?: RequestInit) => ({ value: (JSON.parse(String(init?.body)) as { cap: number | null }).cap ?? 80, source: "control", default: 80 }),
  "PATCH /api/settings/codex-throttle": withThrottle({ enabled: false, source: "control" }),
  "PATCH /api/settings/codex-call-cap": withThrottle(),
});
const patchBodies = (path: string) => fake!.calls.filter((c) => c.init?.method === "PATCH" && String(c.url).includes(path)).map((c) => JSON.parse(String(c.init?.body)));

test("Claude 하루 세션 제한: 「제한 변경」으로 새 값 저장, 「기본값으로」는 null", async () => {
  tokenStore.set("tok");
  fake = installFakeFetch(settingsRoutes());
  const { container } = render(<Usage />);
  await flush();
  const claude = container.querySelector('[aria-label="Claude 하루 세션 제한"]') as HTMLElement;
  expect(claude.textContent).toContain("설정 파일");
  await act(async () => buttonIn(claude, "제한 변경")!.click());
  typeInto(claude.querySelector('input[type="number"]') as HTMLInputElement, "120");
  await act(async () => buttonIn(claude, "저장")!.click());
  await flush();
  await act(async () => buttonIn(claude, "제한 변경")!.click());
  await act(async () => buttonIn(claude, "기본값으로")!.click());
  await flush();
  expect(patchBodies("/settings/session-cap")).toEqual([{ cap: 120 }, { cap: null }]);
});

test("Codex 사용량 조절: 상태 한 줄과 Confirm 뒤 PATCH /settings/codex-throttle {enabled:false}", async () => {
  tokenStore.set("tok");
  fake = installFakeFetch(settingsRoutes());
  const { container } = render(<Usage />);
  await flush();
  const section = container.querySelector('[aria-label="Codex 사용량"]') as HTMLElement;
  expect(section.textContent).toContain("사용량 조절 켜짐");
  await act(async () => buttonIn(section, "조절 끄기")!.click());
  const dlg = document.querySelector('[role="dialog"]') as HTMLElement;
  await act(async () => buttonIn(dlg, "확인")!.click());
  await flush();
  expect(patchBodies("/settings/codex-throttle")).toEqual([{ enabled: false }]);
});

test("Codex 하루 호출 제한 변경과, 사용률 초과 때 새 분석은 Claude 로 넘어간다는 안내", async () => {
  tokenStore.set("tok");
  fake = installFakeFetch(settingsRoutes(withThrottle({ state: "over", reason: "Codex 5시간 사용률 97% ≥ 95%" })));
  const { container } = render(<Usage />);
  await flush();
  const section = container.querySelector('[aria-label="Codex 사용량"]') as HTMLElement;
  expect(section.textContent).toContain("97%");
  expect(section.textContent).toContain("새 분석은 Claude");
  const cap = section.querySelector('[aria-label="Codex 하루 호출 제한"]') as HTMLElement;
  await act(async () => buttonIn(cap, "제한 변경")!.click());
  typeInto(cap.querySelector('input[type="number"]') as HTMLInputElement, "30");
  await act(async () => buttonIn(cap, "저장")!.click());
  await flush();
  expect(patchBodies("/settings/codex-call-cap")).toEqual([{ cap: 30 }]);
});
