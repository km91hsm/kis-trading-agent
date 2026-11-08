import { act } from "react";
import { afterEach, beforeEach, expect, test } from "vitest";
import { SettingsPanel } from "../system/SettingsPanel";
import { tokenStore } from "../../lib/api";
import { clearResourceCache } from "../../lib/cache";
import { setEventsClient } from "../../lib/sse";
import { installFakeFetch, type FakeFetch } from "../../test/fakeFetch";
import { fixtureSettings } from "../../test/fixtures/system";
import { flush, render } from "../../test/render";

let ff: FakeFetch | undefined;
beforeEach(() => {
  clearResourceCache();
  tokenStore.set("test-token");
  setEventsClient({ subscribe: () => () => {}, status: () => ({ connected: true, since: 0 }), close() {} });
});
afterEach(() => { ff?.restore(); tokenStore.set(""); document.body.innerHTML = ""; });

const codex = {
  enabled: false, source: "yaml", model: null, available: true, readiness: "installed",
  effective_rounds: 2, applies_to: "next_new_research", roles: { bull: "claude", bear: "claude" },
  usage: { provider: "codex", calls: 0, errors: 0, duration_ms: 0, input_tokens: null, output_tokens: null, cost_usd: null, subscription_utilization: null },
  last_error: null,
};
const scalp = {
  accounts: [
    { id: "paper-main", env: "paper", markets: ["KR"], enabled: true, source: "yaml", effective: true, reason: null },
    { id: "real-1", env: "real", markets: ["KR"], enabled: false, source: "control", effective: false, reason: "꺼짐" },
  ],
  judge: true, max_positions: 3, max_weight_pct: 5, default_paper: true,
};
const patches = () => ff!.calls.filter((c) => c.init?.method === "PATCH").map((c) => ({ url: c.url, body: JSON.parse(String(c.init?.body)) }));
const sw = (c: HTMLElement, id: string) => c.querySelector(`[role="switch"][aria-label="${id} 단타"]`) as HTMLButtonElement;

test("renders rows, paper toggle patches without confirm, real enable needs id, reset sends null", async () => {
  ff = installFakeFetch({
    "GET /api/settings": fixtureSettings(),
    "GET /api/settings/codex-debate": codex,
    "GET /api/settings/scalp": scalp,
    "PATCH /api/settings/scalp/paper-main": scalp,
    "PATCH /api/settings/scalp/real-1": scalp,
  });
  const { container } = render(<SettingsPanel />);
  await flush();
  expect(container.textContent).toContain("paper-main");
  expect(container.textContent).toContain("모의");
  expect(container.textContent).toContain("실전");
  expect(container.textContent).toContain("동작");
  expect(container.textContent).toContain("꺼짐");
  await act(async () => sw(container, "paper-main").click());
  await flush();
  expect(patches()).toEqual([{ url: expect.stringContaining("/settings/scalp/paper-main"), body: { enabled: false } }]);
  expect(document.querySelector('[role="dialog"]')).toBeNull();

  await act(async () => sw(container, "real-1").click());
  await flush();
  const dlg = document.querySelector('[role="dialog"]') as HTMLElement;
  expect(dlg).not.toBeNull();
  const input = dlg.querySelector("input") as HTMLInputElement;
  const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")!.set!;
  await act(async () => { setter.call(input, "real-1"); input.dispatchEvent(new Event("input", { bubbles: true })); });
  const ok = Array.from(dlg.querySelectorAll("button")).find((b) => !b.disabled && b.textContent !== "취소") as HTMLButtonElement;
  await act(async () => ok.click());
  await flush();
  expect(patches()[1].body).toEqual({ enabled: true, confirm: "real-1" });

  const reset = Array.from(container.querySelectorAll("button")).find((b) => b.textContent === "기본값으로") as HTMLButtonElement;
  expect(reset).toBeTruthy();
  await act(async () => reset.click());
  await flush();
  expect(patches()[2]).toMatchObject({ url: expect.stringContaining("real-1"), body: { enabled: null } });
});

test("shows error on 400", async () => {
  ff = installFakeFetch({
    "GET /api/settings": fixtureSettings(),
    "GET /api/settings/codex-debate": codex,
    "GET /api/settings/scalp": scalp,
    "PATCH /api/settings/scalp/paper-main": () => new Response(JSON.stringify({ detail: "확인 필요" }), { status: 400 }),
  });
  const { container } = render(<SettingsPanel />);
  await flush();
  await act(async () => sw(container, "paper-main").click());
  await flush();
  expect(container.querySelector('[role="alert"]')?.textContent).toContain("확인 필요");
});

const cell = (c: HTMLElement, id: string, col: number) => {
  const row = Array.from(c.querySelectorAll("tr")).find((r) => r.querySelector(`[role="switch"][aria-label="${id} 단타"]`))!;
  return row.querySelectorAll("td")[col] as HTMLElement;
};

test("real account ON -> OFF sends enabled:false directly (no confirm); reset hidden unless source is control; reason in 실효 cell", async () => {
  const live = {
    accounts: [
      { id: "paper-main", env: "paper", markets: ["KR"], enabled: true, source: "yaml", effective: true, reason: null },
      { id: "real-1", env: "real", markets: ["KR"], enabled: true, source: "default", effective: false, reason: "전체 일시정지" },
    ],
    judge: true, max_positions: 3, max_weight_pct: 5, default_paper: true,
  };
  ff = installFakeFetch({
    "GET /api/settings": fixtureSettings(),
    "GET /api/settings/codex-debate": codex,
    "GET /api/settings/scalp": live,
    "PATCH /api/settings/scalp/real-1": live,
  });
  const { container } = render(<SettingsPanel />);
  await flush();
  // 기본값으로 버튼은 source === "control" 일 때만
  expect(Array.from(container.querySelectorAll("button")).some((b) => b.textContent === "기본값으로")).toBe(false);
  // 실효 칸에는 사유 문구가 그대로(스위치의 「꺼짐 · 켜기」 라벨과 별개)
  const eff = cell(container, "real-1", 3);
  expect(eff.textContent).toBe("전체 일시정지");
  expect(cell(container, "paper-main", 3).textContent).toBe("동작");
  await act(async () => sw(container, "real-1").click());
  await flush();
  expect(document.querySelector('[role="dialog"]')).toBeNull();
  expect(patches()).toEqual([{ url: expect.stringContaining("/settings/scalp/real-1"), body: { enabled: false } }]);
});

test("reason text is shown in the 실효 cell for a switched-off account", async () => {
  ff = installFakeFetch({
    "GET /api/settings": fixtureSettings(),
    "GET /api/settings/codex-debate": codex,
    "GET /api/settings/scalp": scalp,
  });
  const { container } = render(<SettingsPanel />);
  await flush();
  expect(cell(container, "real-1", 3).textContent).toBe("꺼짐");
  expect(cell(container, "real-1", 2).textContent).toBe("꺼짐 · 켜기");
});
