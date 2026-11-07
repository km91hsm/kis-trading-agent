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
