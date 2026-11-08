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
afterEach(() => { ff?.restore(); tokenStore.set(""); });

const settings = (enabled = false) => ({
  enabled, source: "control", model: null, available: true, readiness: "installed",
  effective_rounds: 2, applies_to: "next_new_research",
  roles: { bull: "claude", bear: enabled ? "codex" : "claude" },
  usage: { provider: "codex", calls: 0, errors: 0, duration_ms: 0, input_tokens: null, output_tokens: null, cost_usd: null, subscription_utilization: null },
  last_error: null,
});

test("Codex participation can be enabled and disabled, persists server result, and explains next research scope", async () => {
  let enabled = false;
  ff = installFakeFetch({
    "GET /api/settings": fixtureSettings(),
    "GET /api/settings/codex-debate": () => settings(enabled),
    "PATCH /api/settings/codex-debate": (_url: URL, init?: RequestInit) => {
      enabled = (JSON.parse(String(init?.body)) as { enabled: boolean }).enabled;
      return settings(enabled);
    },
  });
  const { container } = render(<SettingsPanel />);
  await flush();
  const toggle = () => container.querySelector('[role="switch"][aria-label="Codex 토론 참여"]') as HTMLButtonElement;
  expect(toggle()).not.toBeNull();
  expect(toggle().getAttribute("aria-checked")).toBe("false");
  expect(container.textContent).toContain("다음 새 분석부터");
  expect(container.textContent).toContain("로그인은 실행 시 확인");
  await act(async () => toggle().click());
  await flush();
  expect(toggle().getAttribute("aria-checked")).toBe("true");
  expect(container.textContent).toContain("1판은 Claude 강세 · Codex 약세, 2판은 Codex 강세 · Claude 약세");
  expect(container.textContent).toContain("사용률 미제공");
  expect(container.textContent).toContain("도중에 Codex 가 실패하면 그 분석은 실패로 끝나고, 같은 분석을 다시 할 때 Claude 2인 토론으로 진행합니다");
  expect(container.textContent).not.toContain("새 결정을 보류");
  await act(async () => toggle().click());
  await flush();
  expect(toggle().getAttribute("aria-checked")).toBe("false");
  expect(ff.calls.filter((c) => c.init?.method === "PATCH").map((c) => JSON.parse(String(c.init?.body)))).toEqual([{ enabled: true }, { enabled: false }]);
});

test("shows both lanes when cross debate is on", async () => {
  ff = installFakeFetch({
    "GET /api/settings": fixtureSettings(),
    "GET /api/settings/codex-debate": { ...settings(true), lanes: [{ lane: "a", bull: "claude", bear: "codex" }, { lane: "b", bull: "codex", bear: "claude" }], version: 2 },
  });
  const { container } = render(<SettingsPanel />);
  await flush();
  expect(container.textContent).toContain("1판 Claude 강세 · Codex 약세");
  expect(container.textContent).toContain("2판 Codex 강세 · Claude 약세");
  expect(container.textContent).toContain("도중에 Codex 가 실패하면 그 분석은 실패로 끝나고, 같은 분석을 다시 할 때 Claude 2인 토론으로 진행합니다");
  expect(container.textContent).not.toContain("새 결정을 보류");
});

test("Codex failed save preserves disabled state and displays error without claiming success", async () => {
  ff = installFakeFetch({
    "GET /api/settings": fixtureSettings(),
    "GET /api/settings/codex-debate": settings(),
    "PATCH /api/settings/codex-debate": () => new Response(JSON.stringify({ detail: "설정을 저장하지 못했습니다" }), { status: 500 }),
  });
  const { container } = render(<SettingsPanel />);
  await flush();
  const toggle = container.querySelector('[role="switch"]') as HTMLButtonElement;
  expect(toggle).not.toBeNull();
  await act(async () => toggle.click());
  await flush();
  expect(toggle.getAttribute("aria-checked")).toBe("false");
  expect(container.querySelector('[role="alert"]')?.textContent).toContain("설정을 저장하지 못했습니다");
});
