import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, expect, test } from "vitest";
import { OperationsSummary } from "../today/OperationsSummary";
import { SettingsPanel } from "../system/SettingsPanel";
import { clearResourceCache } from "../../lib/cache";
import { setEventsClient } from "../../lib/sse";
import { installFakeFetch, type FakeFetch } from "../../test/fakeFetch";
import { render, flush } from "../../test/render";

let ff: FakeFetch;
beforeEach(() => { clearResourceCache(); setEventsClient({ subscribe: () => () => {}, status: () => ({ connected: true, since: 0 }), close() {} }); });
afterEach(() => ff?.restore());
const usage = { throttle: { enabled: true, state: "soft", reason: "구독 사용률 제한" }, windows: { today: { sessions: 2, cap: 80 } }, caps_by_kind: [] };
const routes = () => ({
  "GET /api/system/market-clock": { markets: { KR: { schedule_open: false }, US: { schedule_open: false } } },
  "GET /api/system/usage": usage,
  "GET /api/system/saveticker": { configured: true, health: "healthy", blocked: false, service: { state: "collecting" }, lanes: {} },
  "GET /api/settings/codex-debate": { enabled: false, available: true, usage: { calls: 0, errors: 0, duration_ms: 0 }, roles: {}, effective_rounds: 1 },
});

test("operational overview separates throttled analysis from healthy collection and links settings", async () => {
  ff = installFakeFetch(routes());
  const { container } = render(<MemoryRouter><OperationsSummary historical /></MemoryRouter>);
  await flush();
  expect(container.textContent).toContain("현재 운영 상태");
  expect(container.textContent).toContain("선택한 과거 날짜의 상태가 아닙니다");
  expect(container.textContent).toContain("새 분석 제한");
  expect(container.textContent).toContain("구독 사용률 제한");
  expect(container.textContent).toContain("수집 중");
  expect(container.textContent).toContain("Codex 꺼짐");
  expect(container.querySelector('a[href="/system/admin#settings"]')).not.toBeNull();
});

test("missing operational resources are unknown, never healthy or allowed", async () => {
  ff = installFakeFetch({});
  const { container } = render(<MemoryRouter><OperationsSummary /></MemoryRouter>);
  await flush();
  expect(container.textContent).toContain("확인 필요");
  expect(container.textContent).not.toContain("수집 중");
  expect(container.textContent).not.toContain("호출 한도 내");
});

test("analysis daily and per-kind caps are shown even when throttle is disabled", async () => {
  ff = installFakeFetch({ ...routes(), "GET /api/system/usage": { ...usage, throttle: { enabled: false, state: "ok" }, caps_by_kind: [{ kind: "analyze", used: 3, cap: 3 }] } });
  const { container } = render(<MemoryRouter><OperationsSummary /></MemoryRouter>);
  await flush();
  expect(container.textContent).toContain("종목 분석 상한 도달");
  expect(container.textContent).not.toContain("호출 한도 내");
});

test("risk overview displays configured numeric limits rather than only presence", async () => {
  ff = installFakeFetch({ ...routes(), "GET /api/settings": { markets: ["KR"], risk: { max_position_pct: 0.25, max_positions: 5, daily_loss_stop_pct: -0.05, stop_loss_pct: -0.15, risk_per_trade_pct: 0.02 }, debate_rounds: 1, models: {}, llm: { daily_session_cap: 80, budget_usd: 5 }, web: { bind: "127.0.0.1", port: 8787, tailscale_hosts_count: 0, tailscale_users_count: 0 } } });
  const { container } = render(<MemoryRouter><SettingsPanel /></MemoryRouter>);
  await flush();
  expect(container.textContent).toContain("종목당 최대 비중");
  expect(container.textContent).toContain("25%");
  expect(container.textContent).toContain("하루 손실 중단");
  expect(container.textContent).toContain("-5%");
  expect(container.textContent).toContain("기본 손절");
});


test("소수 리스크 설정을 정수 백분율로 반올림하지 않는다", async () => {
  ff = installFakeFetch({ ...routes(), "GET /api/settings": { markets: ["KR"], risk: { max_position_pct: 0.0075, risk_per_trade_pct: 0.0025, daily_loss_stop_pct: -0.005 }, debate_rounds: 1, models: {}, llm: { daily_session_cap: 80, budget_usd: 5 }, web: { bind: "127.0.0.1", port: 8787, tailscale_hosts_count: 0, tailscale_users_count: 0 } } });
  const { container } = render(<MemoryRouter><SettingsPanel /></MemoryRouter>);
  await flush();
  expect(container.textContent).toContain("0.75%");
  expect(container.textContent).toContain("0.25%");
  expect(container.textContent).toContain("-0.5%");
});
