import { Link, MemoryRouter, Route, Routes } from "react-router-dom";
import { act } from "react";
import { afterEach, beforeEach, expect, test } from "vitest";
import { Decision } from "../decision/Decision.tsx";
import type { DecisionInputs } from "../decision/InputEvidence";
import { clearResourceCache } from "../../lib/cache";
import { setEventsClient } from "../../lib/sse";
import { installFakeFetch, type FakeFetch } from "../../test/fakeFetch";
import { flush, render } from "../../test/render";
import { fixtureSpotDetail, fixtureSpotTrace } from "../../test/fixtures/decision";

let ff: FakeFetch;
beforeEach(() => {
  clearResourceCache();
  setEventsClient({ subscribe: () => () => {}, status: () => ({ connected: true, since: 0 }), close() {} });
});
afterEach(() => ff?.restore());

const inputs = (): DecisionInputs => ({
  decision_id: 7,
  snapshot: { status: "present", asof: "2026-11-04T01:00:00Z" },
  delivery: { status: "unrecorded", receipts: [], unreadable_count: 0, unlisted_count: 0 },
  reconstruction: { status: "current_rules" },
  groups: [{ id: "news", stored_count: 2, reconstructed_count: 1, omitted_count: 1, status: "present", received_at: "2026-11-04T01:00:00Z", listed_count: 2 }],
  items: [
    { id: "news.stock:0", group: "news", refs: ["news:17"], title: "합성 입력 A", source: "합성 출처", url: "https://example.test/17", received_at: "2026-11-04T01:00:00Z", source_asof: "2026-11-03", cited: true, excerpt_truncated: false },
    { id: "news.stock:1", group: "news", refs: ["news:18"], title: "합성 입력 B", source: "합성 출처", url: "javascript:alert(1)", received_at: null, source_asof: null, cited: false, excerpt_truncated: false },
  ],
});

async function mount(input = inputs(), debate: unknown[] = []) {
  ff = installFakeFetch({
    "GET /api/decisions/spot/7": fixtureSpotDetail(),
    "GET /api/decisions/spot/7/trace": { ...fixtureSpotTrace(), debate },
    "GET /api/decisions/spot/7/inputs": input,
  });
  render(<MemoryRouter initialEntries={["/decisions/spot/7"]}><Routes><Route path="/decisions/:asset/:id" element={<Decision />} /></Routes></MemoryRouter>);
  await flush();
}

test("당시 수집, 현재 규칙 재구성, 전달 미기록, 모델 인용을 구분하고 안전한 원문만 연결한다", async () => {
  await mount();
  const section = document.getElementById("inputs")!;
  expect(section?.textContent).toContain("전달용 응답 미기록");
  expect(section.textContent).toContain("현재 규칙 재구성");
  expect(section.textContent).toContain("당시 전달을 증명하지 않습니다");
  expect(section.textContent).toContain("보고서·토론에서 인용");
  expect(section.textContent).toContain("인용 미기록");
  expect(section.textContent).toContain("합성 입력 A");
  expect(section.textContent).toContain("합성 출처");
  const links = Array.from(section.querySelectorAll("a"));
  expect(links.some(a => a.href === "https://example.test/17")).toBe(true);
  expect(links.some(a => a.getAttribute("href")?.startsWith("javascript:"))).toBe(false);
  const external = links.find(a => a.href === "https://example.test/17")!;
  expect(external.rel).toContain("noopener");
  expect(section.querySelector("pre")).toBeNull();
});

test("같은 쟁점의 주장·반론·재답변을 연결하고 공급자·합의는 추정하지 않는다", async () => {
  const turn = { stance: "BULLISH", timing: null, timing_note: null };
  await mount(inputs(), [
    { ...turn, round: 0, side: "bull", conviction: .8, provider: "claude", model: "model-a", issues: [{ issue_id: "bull:growth", topic: "합성 성장 쟁점", claim: "합성 매출 증가 주장", evidence: ["news:17"] }] },
    { ...turn, round: 1, side: "bear", conviction: .6, provider: "codex", model: "model-b", responses: [{ issue_id: "bull:growth", response: "합성 이익률 반론", evidence: ["news:18"] }], issues: [{ issue_id: "bear:rates", topic: "별도 금리 쟁점", claim: "별도 금리 주장", evidence: ["macro:us10y"] }] },
    { ...turn, round: 2, side: "bull", conviction: .7, provider: "claude", model: "model-a", replies: [{ issue_id: "bull:growth", response: "합성 비용 재답변", evidence: ["news:17"] }] },
    { ...turn, round: 0, side: "bear", conviction: .5 },
  ]);
  const comparison = document.getElementById("comparison")!;
  expect(comparison?.textContent).toContain("쟁점별 비교");
  const issue = comparison.querySelector('[data-issue-id="a:bull:growth"]')!;
  expect(issue.textContent).toContain("합성 매출 증가 주장");
  expect(issue.textContent).toContain("합성 이익률 반론");
  expect(issue.textContent).toContain("합성 비용 재답변");
  expect(issue.textContent).not.toContain("별도 금리 주장");
  expect(issue.textContent).toContain("합의 미기록");
  expect(issue.textContent).toContain("Claude");
  expect(issue.textContent).toContain("Codex");
  expect(comparison.textContent).toContain("공급자 미기록");
  expect(comparison.textContent).toContain("80% → 70%");
  const ref = Array.from(issue.querySelectorAll("a")).find(a => a.textContent?.includes("news:17"))!;
  expect(ref.getAttribute("href")).toBe("#input-news.stock%3A0");
  expect(document.getElementById("input-news.stock%3A0")).not.toBeNull();
});

test("빈 입력과 토론을 정상 활용 또는 합의로 표시하지 않는다", async () => {
  const data = inputs();
  data.snapshot.status = "missing";
  data.groups = [];
  data.items = [];
  await mount(data);
  expect(document.getElementById("inputs")?.textContent).toContain("저장 입력 없음");
  expect(document.getElementById("comparison")?.textContent).toContain("구조화된 쟁점 기록 없음");
});

test("전달용 기록의 건수와 식별자를 입력으로 연결하고 실제 인용으로 승격하지 않는다", async () => {
  const data = inputs();
  data.items[0].cited = false;
  data.delivery = { status: "recorded", receipts: [{ stage: "trader", provider: null, recorded_at: "2026-11-04T01:00:00Z", context_hash: "a".repeat(64), truncated: true, groups: [{ id: "news", count: 1, refs: ["news:17"], identifiers_omitted: 0 }] }], unreadable_count: 1, unlisted_count: 0 };
  await mount(data);
  const section = document.getElementById("inputs")!;
  expect(section.textContent).toContain("전달용 도구 응답·호출 준비 기록 1개");
  expect(section.textContent).toContain("뉴스 1건");
  expect(section.textContent).toContain("인용 미기록");
  expect(section.textContent).not.toContain("보고서·토론에서 인용");
  const receipt = section.querySelector(".input-receipt")!;
  expect(receipt.textContent).toContain("축약됨");
  expect(receipt.querySelector('a[href="#input-news.stock%3A0"]')).not.toBeNull();
});

test("절 이름 근거는 기사 하나를 인용했다고 과장하지 않고 입력 묶음에 연결한다", async () => {
  const data = inputs();
  data.groups[0].refs = ["news_macro.news"];
  data.groups[0].cited = true;
  data.items.forEach(item => item.cited = false);
  await mount(data, [{ round: 0, side: "bull", conviction: .5, issues: [{ issue_id: "bull:one", topic: "묶음 쟁점", claim: "묶음 주장", evidence: ["news_macro.news"] }] }]);
  const comparison = document.getElementById("comparison")!;
  const link = comparison.querySelector('a[href="#input-group-news"]');
  expect(link?.textContent).toContain("뉴스 입력 묶음");
  expect(document.getElementById("input-group-news")).not.toBeNull();
  expect(document.querySelector(".input-items")?.textContent).not.toContain("보고서·토론에서 인용");
});


test("다른 결정으로 이동 중이거나 입력 조회가 실패하면 이전 결정 자료와 근거를 숨긴다", async () => {
  let complete: (value: Response) => void = () => {};
  const pending = new Promise<Response>(resolve => { complete = resolve; });
  const debate = [{ round: 0, side: "bull", stance: "BULLISH", conviction: .8, issues: [{ issue_id: "bull:growth", topic: "다음 결정 쟁점", claim: "다음 결정 주장", evidence: ["news:17"] }] }];
  ff = installFakeFetch({
    "GET /api/decisions/spot/7": fixtureSpotDetail(),
    "GET /api/decisions/spot/7/trace": fixtureSpotTrace(),
    "GET /api/decisions/spot/7/inputs": inputs(),
    "GET /api/decisions/spot/8": fixtureSpotDetail({ decision: { ...fixtureSpotDetail().decision, id: 8 } }),
    "GET /api/decisions/spot/8/trace": fixtureSpotTrace({ debate }),
    "GET /api/decisions/spot/8/inputs": () => pending,
  });
  const { container } = render(<MemoryRouter initialEntries={["/decisions/spot/7"]}><Link to="/decisions/spot/8">다음 결정</Link><Routes><Route path="/decisions/:asset/:id" element={<Decision />} /></Routes></MemoryRouter>);
  await flush();
  expect(document.getElementById("inputs")?.textContent).toContain("합성 입력 A");
  act(() => container.querySelector<HTMLAnchorElement>('a[href="/decisions/spot/8"]')!.click());
  await flush();
  expect(document.getElementById("inputs")?.textContent).not.toContain("합성 입력 A");
  expect(document.getElementById("comparison")?.querySelector('a[href^="#input-"]')).toBeNull();
  await act(async () => complete(new Response(JSON.stringify({ detail: "입력 읽기 실패" }), { status: 503 })));
  await flush();
  expect(document.getElementById("inputs")?.textContent).toContain("입력 읽기 실패");
  expect(document.getElementById("inputs")?.textContent).not.toContain("합성 입력 A");
});


test("하위 원천의 오류와 정상 0건을 구분하고 일부 자료도 오류 기록을 보인다", async () => {
  const data = inputs();
  data.groups = [
    { id: "technical", stored_count: 1, reconstructed_count: null, omitted_count: null, status: "partial", received_at: null, listed_count: 1, sections: [
      { id: "technical:quote", count: 0, status: "error", source: "fixture-q", received_at: "2026-11-04T01:00:00Z" },
      { id: "technical:indicators", count: 1, status: "present", source: "fixture-i", received_at: "2026-11-04T01:00:00Z" },
    ] },
    { id: "community", stored_count: 0, reconstructed_count: null, omitted_count: null, status: "error", received_at: null, listed_count: 0, sections: [{ id: "community", count: 0, status: "error", source: "fixture-c", received_at: null }] },
    { id: "events", stored_count: 0, reconstructed_count: 0, omitted_count: 0, status: "empty", received_at: null, listed_count: 0, sections: [{ id: "events", count: 0, status: "empty", source: null, received_at: null }] },
  ];
  data.items = [];
  await mount(data);
  const section = document.getElementById("inputs")!;
  expect(section.textContent).toContain("일부 자료·오류 기록");
  const detail = Array.from(section.querySelectorAll("details")).find(node => node.querySelector("summary")?.textContent === "원천별 수집 결과")!;
  expect(detail?.textContent).toContain("technical:quote");
  expect(detail.textContent).toContain("fixture-q");
  expect(detail.textContent).toContain("자료 없음·오류 기록");
  expect(detail.textContent).toContain("events");
  expect(detail.textContent).toContain("0건");
});


test("의도적인 수집 생략은 오류 경고 없이 한글로 표시한다", async () => {
  const data = inputs();
  data.groups = [{ id: "sentiment", stored_count: 0, reconstructed_count: null, omitted_count: null, status: "skipped", received_at: null, listed_count: 0, sections: [
    { id: "sentiment:short_interest", count: 0, status: "skipped", source: null, received_at: null },
    { id: "sentiment:trends", count: 0, status: "skipped", source: null, received_at: null },
  ] }];
  data.items = [];
  await mount(data);
  const section = document.getElementById("inputs")!;
  expect(section.textContent).toContain("의도적 생략");
  expect(section.textContent).not.toContain("자료 없음·오류 기록");
  expect(section.textContent).not.toContain("일부 자료·오류 기록");
  expect(section.querySelector('.chip[data-tone="warn"]')).toBeNull();
});

test("내부 원천 실패와 성공을 표시하되 원천별 건수를 추정하지 않는다", async () => {
  const data = inputs();
  data.groups[0].status = "partial";
  data.groups[0].sections = [{ id: "news", count: 2, status: "partial", source: null, received_at: null, source_outcomes: [
    { source: "google_rss", status: "error", count: null },
    { source: "naver", status: "success", count: null },
  ] }];
  await mount(data);
  const detail = Array.from(document.querySelectorAll("#inputs details")).find(node => node.querySelector("summary")?.textContent === "원천별 수집 결과")!;
  expect(detail?.textContent).toContain("google_rss: 실패");
  expect(detail.textContent).toContain("naver: 성공");
  expect(detail.textContent).toContain("원천별 건수 미기록");
  expect(detail.textContent).not.toContain("naver: 성공 2건");
});

test("issues with same id in two lanes stay separate", async () => {
  const turn = { stance: "BULLISH", timing: null, timing_note: null, round: 0, side: "bull", conviction: .8 };
  await mount(inputs(), [
    { ...turn, lane: "a", provider: "claude", model: "m-a", issues: [{ issue_id: "bull:growth", topic: "1판 성장 쟁점", claim: "1판 주장", evidence: [] }] },
    { ...turn, lane: "b", provider: "codex", model: "m-b", issues: [{ issue_id: "bull:growth", topic: "2판 성장 쟁점", claim: "2판 주장", evidence: [] }] },
  ]);
  const comparison = document.getElementById("comparison")!;
  expect(comparison.textContent).toContain("1판 성장 쟁점");
  expect(comparison.textContent).toContain("2판 성장 쟁점");
  expect(comparison.querySelectorAll("[data-issue-id]")).toHaveLength(2);
  expect(comparison.textContent).toContain("2판 Codex (강세 측)");
});
