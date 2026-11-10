import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, expect, test } from "vitest";
import { clearResourceCache } from "../../lib/cache";
import { setEventsClient, type EventsClient } from "../../lib/sse";
import { installFakeFetch, type FakeFetch } from "../../test/fakeFetch";
import { fixtureJobs, fixtureRuns } from "../../test/fixtures/system";
import { flush, render } from "../../test/render";
import { Runs } from "./Runs";

let fake: FakeFetch | null = null;
const events: EventsClient = { subscribe: () => () => {}, status: () => ({ connected: true, since: 0 }), close() {} };
beforeEach(() => {
  clearResourceCache();
  setEventsClient(events);
});
afterEach(() => fake?.restore());

test("후속 정상 시각과 범위 미확인을 원문 오류와 함께 표시한다", async () => {
  const base = fixtureRuns()[1];
  fake = installFakeFetch({
    "GET /api/jobs": fixtureJobs(),
    "GET /api/runs": [
      { ...base, id: 1, outcome: "followed_by_success", followup_at: "2026-11-05T12:00:00+09:00", scope_confirmed: true },
      { ...base, id: 2, outcome: "failed", followup_at: null, scope_confirmed: false },
      { ...base, id: 3, outcome: "interrupted", error: "세션 중단 (종료 요청)", followup_at: null, scope_confirmed: false },
    ],
  });
  const { container } = render(<MemoryRouter><Runs /></MemoryRouter>);
  await flush();
  const rows = Array.from(container.querySelectorAll("tbody tr"));
  expect(rows[0].textContent).toContain("후속 정상");
  expect(rows[0].textContent).toContain("후속 정상 시각");
  expect(rows[0].textContent).toContain("브로커 오류");
  expect(rows[1].textContent).toContain("확인 필요");
  expect(rows[1].textContent).toContain("대상 범위 미확인");
  expect(rows[2].textContent).toContain("중단");
  expect(rows[2].textContent).toContain("세션 중단 (종료 요청)");
  expect(container.textContent).toContain("원인 해결을 보장하지 않습니다");
});
