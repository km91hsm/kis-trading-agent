import { act } from "react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { tokenStore } from "../../lib/api";
import { noteServerTime } from "../../lib/clock";
import { clearResourceCache } from "../../lib/cache";
import { setEventsClient } from "../../lib/sse";
import { installFakeFetch, type FakeFetch } from "../../test/fakeFetch";
import { flush, render } from "../../test/render";
import { RiskPolicy, RiskPolicySummary } from "../system/RiskPolicy";
import { policyLimits } from "../system/risk-policy";

const market = (over: Record<string, unknown> = {}) => ({
  market: "KR", regime: "bear", asof: "2026-11-10", signals: { ma_below: true, vol_high: false },
  values: { close: 2500, ma: 2700, momentum: -0.03, realized_vol: 0.2, vix: null }, streak: 2, exit_streak_days: 3,
  ai_view: { date: "2026-11-10", stance: "RISK_OFF", confidence: 0.72, rationale: "합성 AI 방어 근거" },
  policy: { mode: "RISK_ON", source: "manual", reason: "합성 운영 사유", expires_at: "2026-11-11T00:00:00+09:00", date: "2026-11-10" },
  effective: { stance: "RISK_ON", entry: "limited", reason: "수동 정책 범위 안에서 허용", max_position_pct: 0.1, max_positions: 2 },
  accounts: [{ id: "paper-1", env: "paper" }],
  history: [{ at: "2026-11-10T10:00:00+09:00", mode: "RISK_ON", reason: "합성 이전 사유", actor: "local", expires_at: "2026-11-11T00:00:00+09:00" }], ...over,
});
let ff: FakeFetch;
beforeEach(() => {
  clearResourceCache(); tokenStore.set("test-token");
  setEventsClient({ subscribe: () => () => {}, status: () => ({ connected: true, since: 0 }), close() {} });
});
afterEach(() => { ff?.restore(); tokenStore.set(""); vi.useRealTimers(); noteServerTime(new Date(0).toISOString(), 0); });
const patches = () => ff.calls.filter((c) => c.init?.method === "PATCH");
test.each(["blocked", "unknown"])("%s 정책에서는 설정 한도를 매수 허용 범위처럼 표시하지 않는다", async (entry) => {
  ff = installFakeFetch({ "GET /api/settings/risk-policy": { markets: [market({ effective: { stance: "RISK_OFF", entry, reason: "신규 매수 불가", max_position_pct: 0.25, max_positions: 5 } })] } });
  const { container } = render(<RiskPolicySummary />); await flush();
  expect(container.textContent).toContain("신규 매수 불가");
  expect(container.querySelector(".risk-policy-limits")).toBeNull();
});
const button = (c: ParentNode, text: string) => Array.from(c.querySelectorAll("button")).find((b) => b.textContent?.includes(text))!;
async function input(c: ParentNode, label: string, value: string) {
  const node = c.querySelector(`[aria-label="${label}"]`) as HTMLInputElement | HTMLTextAreaElement;
  const proto = node.tagName === "TEXTAREA" ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
  await act(async () => {
    Object.getOwnPropertyDescriptor(proto, "value")!.set!.call(node, value);
    node.dispatchEvent(new Event("input", { bubbles: true }));
  });
}

test("실효 매수 한도와 AI 원본·확신도를 분리하고 마운트 시 쓰지 않는다", async () => {
  ff = installFakeFetch({ "GET /api/settings/risk-policy": { markets: [market()] } });
  const { container } = render(<RiskPolicy />); await flush();
  expect(container.querySelector('[aria-label="국내 실제 매수 정책"]')?.textContent).toContain("제한적 매수");
  expect(container.textContent).toContain("종목당 최대 10%");
  expect(container.textContent).toContain("최대 2종목");
  expect(container.querySelector('[aria-label="국내 AI 원본 판단"]')?.textContent).toContain("방어");
  expect(container.textContent).toContain("72%");
  expect(container.textContent).toContain("합성 AI 방어 근거");
  expect(container.textContent).toContain("2026-11-11");
  expect(container.textContent).toContain("합성 이전 사유");
  expect(container.textContent).toContain("이동평균 아래");
  expect(patches()).toHaveLength(0);
});

test("정책 버튼은 선택만 하고 사유를 채워 명시적으로 저장해야 PATCH한다", async () => {
  const data = { markets: [market()] };
  ff = installFakeFetch({ "GET /api/settings/risk-policy": data, "PATCH /api/settings/risk-policy/KR": data });
  const { container } = render(<RiskPolicy />); await flush();
  await act(async () => button(container, "방어 · 신규 매수 차단").click());
  expect(patches()).toHaveLength(0);
  expect(button(container, "국내 정책 저장").disabled).toBe(true);
  await input(container, "국내 변경 사유", "  위험 노출 축소  ");
  await act(async () => button(container, "국내 정책 저장").click()); await flush();
  expect(JSON.parse(String(patches()[0].init?.body))).toEqual({ mode: "RISK_OFF", reason: "위험 노출 축소" });
  expect(container.querySelector('[role="status"]')?.textContent).toContain("저장했습니다");
  expect(document.activeElement).toBe(container.querySelector('[role="status"]'));
});

test("모든 실전 계좌를 각각 확인해야 자동 복귀도 저장할 수 있다", async () => {
  const data = { markets: [market({ accounts: [{ id: "real-a", env: "real" }, { id: "real-b", env: "real" }] })] };
  ff = installFakeFetch({ "GET /api/settings/risk-policy": data, "PATCH /api/settings/risk-policy/KR": data });
  const { container } = render(<RiskPolicy />); await flush();
  await act(async () => button(container, "자동 · 시장 판단 따르기").click());
  await input(container, "국내 변경 사유", "자동 복귀");
  await input(container, "real-a 확인", "real-a");
  expect(button(container, "국내 정책 저장").disabled).toBe(true);
  await input(container, "real-b 확인", "wrong");
  expect(button(container, "국내 정책 저장").disabled).toBe(true);
  await input(container, "real-b 확인", "real-b");
  await act(async () => button(container, "국내 정책 저장").click()); await flush();
  expect(JSON.parse(String(patches()[0].init?.body))).toEqual({ mode: "AUTO", reason: "자동 복귀", confirmations: { "real-a": "real-a", "real-b": "real-b" } });
});

test("저장 오류를 알리고 사유를 보존해 재시도할 수 있다", async () => {
  ff = installFakeFetch({ "GET /api/settings/risk-policy": { markets: [market()] }, "PATCH /api/settings/risk-policy/KR": () => new Response(JSON.stringify({ detail: "확인 정보가 변경되었습니다" }), { status: 400 }) });
  const { container } = render(<RiskPolicy />); await flush();
  await act(async () => button(container, "방어 · 신규 매수 차단").click());
  await input(container, "국내 변경 사유", "방어 전환");
  await act(async () => button(container, "국내 정책 저장").click()); await flush();
  expect(container.querySelector('[role="alert"]')?.textContent).toContain("확인 정보가 변경되었습니다");
  expect(document.activeElement).toBe(container.querySelector('[role="alert"]'));
  expect((container.querySelector('[aria-label="국내 변경 사유"]') as HTMLTextAreaElement).value).toBe("방어 전환");
  expect(button(container, "국내 정책 저장").disabled).toBe(false);
});

test("과거 날짜 화면에도 현재 정책임을 표시하고 미국 시장을 따로 설명한다", async () => {
  ff = installFakeFetch({ "GET /api/settings/risk-policy": { markets: [market(), market({ market: "US", effective: { stance: "RISK_OFF", entry: "blocked", reason: "미국 위험 신호", max_position_pct: null, max_positions: null } })] } });
  const { container } = render(<RiskPolicySummary historical />); await flush();
  expect(container.textContent).toContain("현재 매수 정책");
  expect(container.textContent).toContain("과거 조회 날짜와 관계없이");
  expect(container.textContent).toContain("미국 위험 신호");
  expect(container.textContent).toContain("신규 매수 차단");
  expect(container.querySelector('a[href="/system/features#regime"]')).not.toBeNull();
  expect(container.querySelector("textarea")).toBeNull();
});

test("조회 실패와 자료 없음은 매수 허용으로 표시하지 않는다", async () => {
  ff = installFakeFetch({ "GET /api/settings/risk-policy": () => new Response(JSON.stringify({ detail: "일시 조회 오류" }), { status: 500 }) });
  const { container, unmount } = render(<RiskPolicySummary />); await flush();
  expect(container.querySelector('[role="alert"]')?.textContent).toContain("확인할 수 없습니다");
  expect(container.textContent).not.toContain("일반 매수 가능"); unmount();
  ff.restore(); clearResourceCache();
  ff = installFakeFetch({ "GET /api/settings/risk-policy": { markets: [] } });
  const empty = render(<RiskPolicySummary />); await flush();
  expect(empty.container.textContent).toContain("매수 허용 여부를 확인할 자료가 없습니다");
});


test("미국 정책 만료는 ET로 표시하고 원본 확신도 0과 자료 없음은 구분한다", async () => {
  ff = installFakeFetch({ "GET /api/settings/risk-policy": { markets: [market({ market: "US", ai_view: { date: "2026-11-10", stance: "NEUTRAL", confidence: 0, rationale: "확신 없음" }, policy: { mode: "RISK_OFF", source: "manual", reason: "미국 방어", expires_at: "2026-11-11T05:00:00Z", date: "2026-11-10" }, effective: { stance: "UNKNOWN", entry: "unknown", reason: "정량 자료 없음", max_position_pct: null, max_positions: null } })] } });
  const { container } = render(<RiskPolicy />); await flush();
  expect(container.textContent).toContain("2026-11-11 00:00 ET");
  expect(container.querySelector('[aria-label="미국 AI 원본 판단"]')?.textContent).toContain("확신도 0%");
  expect(container.querySelector('[aria-label="미국 실제 매수 정책"]')?.textContent).toContain("매수 허용 여부 확인 필요");
});

test("정책 선택을 취소하면 선택 버튼으로 포커스를 되돌린다", async () => {
  ff = installFakeFetch({ "GET /api/settings/risk-policy": { markets: [market()] } });
  const { container } = render(<RiskPolicy />); await flush();
  const choice = button(container, "방어 · 신규 매수 차단");
  await act(async () => choice.click());
  await act(async () => button(container, "취소").click());
  expect(document.activeElement).toBe(choice);
  expect(container.querySelector("textarea")).toBeNull();
  expect(patches()).toHaveLength(0);
});


const autoMarket = () => market({
  policy: { mode: "AUTO", source: "auto", reason: null, expires_at: null, date: "2026-11-11" },
  effective: { stance: "RISK_OFF", entry: "blocked", reason: "만료 후 자동 방어 정책", max_position_pct: null, max_positions: null },
});
const reads = () => ff.calls.filter((call) => call.init?.method === "GET").length;

test.each([
  ["상세", () => <RiskPolicy />],
  ["요약", () => <RiskPolicySummary />],
] as const)("%s: SSE가 정상 연결되어도 서버 시각의 만료에 맞춰 자동 정책을 다시 읽는다", async (_name, view) => {
  vi.useFakeTimers(); vi.setSystemTime(new Date("2026-11-10T14:58:59Z"));
  const expiry = "2026-11-10T15:00:00Z";
  ff = installFakeFetch({ "GET /api/settings/risk-policy": () => {
    const serverTime = new Date(Date.now() + 60_000).toISOString();
    const item = Date.parse(serverTime) >= Date.parse(expiry) ? autoMarket() : market({ policy: { mode: "RISK_ON", source: "manual", reason: "만료 직전", expires_at: expiry, date: "2026-11-10" } });
    return new Response(JSON.stringify({ markets: [item] }), { headers: { "Content-Type": "application/json", "X-Server-Time": serverTime } });
  } });
  const { container } = render(view()); await flush();
  expect(container.querySelector('[aria-label="국내 실제 매수 정책"]')?.textContent).toContain("제한적 매수");
  await act(async () => { await vi.advanceTimersByTimeAsync(1000); }); await flush();
  expect(reads()).toBe(2);
  expect(container.querySelector('[aria-label="국내 실제 매수 정책"]')?.textContent).toContain("만료 후 자동 방어 정책");
  expect(patches()).toHaveLength(0);
});

test("잠든 탭으로 돌아오면 SSE 이벤트 없이도 정책을 다시 읽는다", async () => {
  vi.useFakeTimers(); vi.setSystemTime(new Date("2026-11-10T14:59:00Z"));
  let expired = false;
  ff = installFakeFetch({ "GET /api/settings/risk-policy": () => ({ markets: [expired ? autoMarket() : market()] }) });
  const { container } = render(<RiskPolicySummary />); await flush();
  expired = true;
  await act(async () => { document.dispatchEvent(new Event("visibilitychange")); }); await flush();
  expect(reads()).toBe(2);
  expect(container.textContent).toContain("만료 후 자동 방어 정책");
});

test("이미 만료된 수동 응답이 반복돼도 빠른 재조회 루프 없이 확인 필요를 표시한다", async () => {
  vi.useFakeTimers(); vi.setSystemTime(new Date("2026-11-11T00:00:00Z"));
  ff = installFakeFetch({ "GET /api/settings/risk-policy": { markets: [market()] } });
  const { container } = render(<RiskPolicySummary />); await flush();
  await act(async () => { await vi.advanceTimersByTimeAsync(5000); }); await flush();
  expect(reads()).toBe(2);
  expect(container.querySelector('[role="alert"]')?.textContent).toContain("만료");
  expect(container.querySelector('[aria-label="국내 실제 매수 정책"]')).toBeNull();
  expect(button(container, "다시 조회")).toBeTruthy();
});

test("만료 재조회에 실패하면 지난 실효 정책을 숨기고 수동 재시도를 제공한다", async () => {
  vi.useFakeTimers(); vi.setSystemTime(new Date("2026-11-10T14:59:59Z"));
  ff = installFakeFetch({ "GET /api/settings/risk-policy": () => Date.now() >= Date.parse("2026-11-10T15:00:00Z") ? new Response(JSON.stringify({ detail: "만료 재조회 실패" }), { status: 503 }) : { markets: [market()] } });
  const { container } = render(<RiskPolicySummary />); await flush();
  await act(async () => { await vi.advanceTimersByTimeAsync(1000); }); await flush();
  expect(container.querySelector('[role="alert"]')?.textContent).toContain("만료 재조회 실패");
  expect(container.querySelector('[aria-label="국내 실제 매수 정책"]')).toBeNull();
  expect(button(container, "다시 조회")).toBeTruthy();
});

test("장기 만료를 timeout 정수 범위 안에서 예약해 즉시 재조회하지 않는다", async () => {
  vi.useFakeTimers(); vi.setSystemTime(new Date("2026-11-10T00:00:00Z"));
  ff = installFakeFetch({ "GET /api/settings/risk-policy": { markets: [market({ policy: { mode: "RISK_ON", source: "manual", reason: "합성 장기 경계", expires_at: "2026-12-20T00:00:00Z", date: "2026-11-10" } })] } });
  render(<RiskPolicySummary />); await flush();
  await act(async () => { await vi.advanceTimersByTimeAsync(10_000); }); await flush();
  expect(reads()).toBe(1);
});


test.each([[0.1, "10%"], [0.0075, "0.75%"], [0.0025, "0.25%"]] as const)("실효 비중 %s를 반올림하거나 0을 덧붙이지 않고 %s로 표시한다", (weight, text) => {
  expect(policyLimits({ stance: "RISK_ON", entry: "limited", reason: "설정 한도", max_position_pct: weight, max_positions: 2 })).toBe(`종목당 최대 ${text} · 최대 2종목`);
});
