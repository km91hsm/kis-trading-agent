import { act } from "react";
import { MemoryRouter, useLocation } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, test } from "vitest";
import { clearResourceCache } from "../../lib/cache";
import { setEventsClient, type EventsClient } from "../../lib/sse";
import { installFakeFetch, type FakeFetch } from "../../test/fakeFetch";
import { fixtureJobs, fixtureRequests } from "../../test/fixtures/system";
import { flush, render } from "../../test/render";
import { JobQueue } from "./JobQueue";
import type { JobKind, JobRequest } from "./system";

let fake: FakeFetch | null = null;
const events: EventsClient = { subscribe: () => () => {}, status: () => ({ connected: true, since: 0 }), close() {} };
const request = (over: Partial<JobRequest>): JobRequest => ({ ...(fixtureRequests().requests[0] as JobRequest), ...over });
const kind = (lane: string, name: string, label: string): JobKind => ({ lane, kind: name, label, trading: false, params_schema: {} });

beforeEach(() => {
  clearResourceCache();
  setEventsClient(events);
});
afterEach(() => {
  fake?.restore();
  fake = null;
});

function Location() {
  return <output data-location>{useLocation().search}</output>;
}

async function mount(requests: JobRequest[], { kinds = fixtureJobs().kinds, entry = "/system/jobs" }: { kinds?: JobKind[]; entry?: string } = {}) {
  fake = installFakeFetch({
    "GET /api/jobs": { kinds, jobs: [] },
    "GET /api/jobs/requests": { alive: true, active: requests.filter((r) => r.status === "running" || r.status === "queued").length, requests },
    "GET /api/accounts": [],
  });
  const result = render(<MemoryRouter initialEntries={[entry]}><JobQueue /><Location /></MemoryRouter>);
  await flush();
  return result.container;
}

function ids(root: ParentNode) {
  return Array.from(root.querySelectorAll("table tbody tr")).map((row) => Number(row.querySelector("td")?.textContent));
}

function choose(root: ParentNode, label: string, value: string) {
  const select = root.querySelector(`select[aria-label="${label}"]`) as HTMLSelectElement | null;
  expect(select, `${label} 선택기가 있어야 합니다`).not.toBeNull();
  act(() => {
    select!.value = value;
    select!.dispatchEvent(new Event("change", { bubbles: true }));
  });
}

describe("잡 큐 표시 순서", () => {
  test("요청 기록이 먼저 보이고 수동 실행은 기본적으로 접혀 있다", async () => {
    const root = await mount([]);
    const record = root.querySelector("#job-requests")!;
    const manual = root.querySelector("details")!;
    expect(record).not.toBeNull();
    expect(manual).not.toBeNull();
    expect(manual.open).toBe(false);
    expect(manual.querySelector("summary")?.textContent).toContain("수동 실행");
    expect(record.compareDocumentPosition(manual) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });

  test("진행 우선 정렬은 오래된 실패도 완료 기록보다 먼저 보여 준다", async () => {
    const root = await mount([
      request({ id: 10, status: "ok", finished_at: "2026-11-05T11:00:00+09:00" }),
      request({ id: 11, status: "error", finished_at: "2026-11-04T11:00:00+09:00" }),
    ]);
    expect(ids(root)).toEqual([11, 10]);
  });

  test("입력 순서와 무관하게 실행 중, 우선순위가 높은 대기, 최근 종료 순으로 표시한다", async () => {
    const root = await mount([
      request({ id: 100, status: "ok", finished_at: "2026-11-05T10:00:00+09:00" }),
      request({ id: 30, status: "queued", priority: 5, requested_at: "2026-11-05T08:00:00+09:00" }),
      request({ id: 50, status: "running" }),
      request({ id: 10, status: "queued", priority: 0 }),
      request({ id: 2, status: "error", finished_at: "2026-11-05T11:00:00+09:00" }),
      // 대기 등록 순서는 큐와 같은 id ASC. 시스템 시각이 뒤로 움직여도 바뀌지 않는다.
      request({ id: 20, status: "queued", priority: 5, requested_at: "2026-11-05T09:00:00+09:00" }),
    ]);
    expect(ids(root)).toEqual([50, 20, 30, 10, 2, 100]);
  });

  test("최신 요청순은 시각을 비교하고 같은 시각이면 등록번호로 안정 정렬한다", async () => {
    const root = await mount([
      request({ id: 10, status: "queued", requested_at: "2026-11-05T10:00:00+09:00" }),
      request({ id: 2, status: "ok", requested_at: "2026-11-05T01:30:00Z" }),
      request({ id: 9, status: "running", requested_at: "2026-11-05T10:00:00+09:00" }),
      request({ id: 11, status: "ok", requested_at: null }),
    ]);
    choose(root, "요청 정렬", "recent");
    expect(ids(root)).toEqual([2, 10, 9, 11]);
  });

  test("상태 필터와 정렬 변경은 요청 기록만 첫 페이지로 되돌린다", async () => {
    const rows = Array.from({ length: 65 }, (_, i) => request({ id: i + 1, status: i < 35 ? "queued" : "ok", priority: 0 }));
    const root = await mount(rows, { entry: "/system/jobs?rpage=2&page=3" });
    expect(ids(root)[0]).toBe(31);
    choose(root, "요청 상태", "queued");
    expect(ids(root)).toEqual(Array.from({ length: 30 }, (_, i) => i + 1));
    expect(root.querySelector("[data-location]")?.textContent).toBe("?page=3");
    act(() => (root.querySelector('.pager [data-page="2"]') as HTMLButtonElement).click());
    expect(ids(root)).toEqual([31, 32, 33, 34, 35]);
    choose(root, "요청 정렬", "recent");
    expect(ids(root)[0]).toBe(35);
    expect(root.querySelector("[data-location]")?.textContent).toBe("?page=3");
    choose(root, "요청 상태", "error");
    expect(ids(root)).toEqual([]);
    expect(root.textContent).toContain("선택한 상태의 요청 없음");
  });

  test("수동 실행 그룹과 잡 이름은 API 등록 순서에 영향받지 않는다", async () => {
    const root = await mount([], { kinds: [
      kind("info", "info_one", "정보 수집"),
      kind("slow", "slow_b", "지표 학습"),
      kind("reeval", "reeval_one", "보유 재평가"),
      kind("fast", "fast_one", "잔고 갱신"),
      kind("slow", "slow_a", "시장 분석"),
    ] });
    expect(Array.from(root.querySelectorAll("[data-lane]")).map((group) => group.getAttribute("data-lane"))).toEqual(["fast", "reeval", "slow", "info"]);
    expect(Array.from(root.querySelectorAll('[data-lane="slow"] button')).map((button) => button.textContent)).toEqual(["시장 분석", "지표 학습"]);
  });
});
