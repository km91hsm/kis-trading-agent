import { act } from "react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, test } from "vitest";
import { Decision } from "../decision/Decision.tsx";
import { decisionHeader, roundRows } from "../decision/decision.ts";
import { clearResourceCache } from "../../lib/cache";
import { setEventsClient, type EventsClient } from "../../lib/sse";
import { installFakeFetch, type FakeFetch } from "../../test/fakeFetch";
import { flush, render } from "../../test/render";
import {
  fixtureFoDecisionRow,
  fixtureFoDetail,
  fixtureFoTrace,
  fixtureOutcomes,
  fixtureSpotDecisionRow,
  fixtureSpotDetail,
  fixtureSpotTrace,
} from "../../test/fixtures/decision";

let ff: FakeFetch | null = null;
const stub: EventsClient = { subscribe: () => () => {}, status: () => ({ connected: true, since: 0 }), close() {} };

beforeEach(() => {
  clearResourceCache();
  setEventsClient(stub);
});
afterEach(() => {
  ff?.restore();
  ff = null;
});

type Routes = Parameters<typeof installFakeFetch>[0];
const spotRoutes: Routes = {
  "GET /api/decisions/spot/7": fixtureSpotDetail(),
  "GET /api/decisions/spot/7/trace": fixtureSpotTrace(),
  "GET /api/decisions/spot/7/outcomes": fixtureOutcomes(),
};
const foRoutes: Routes = {
  "GET /api/decisions/fo/3": fixtureFoDetail(),
  "GET /api/decisions/fo/3/trace": fixtureFoTrace(),
};

async function mount(r: Routes, path: string) {
  ff = installFakeFetch(r);
  const out = render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route path="/decisions/:asset/:id" element={<Decision />} />
      </Routes>
    </MemoryRouter>,
  );
  await flush();
  return out;
}
const section = (id: string) => document.getElementById(id) as HTMLElement | null;

describe("결정 상세 순수 함수", () => {
  test("decisionHeader — spot 제목·칩·근거 줄", () => {
    const h = decisionHeader(fixtureSpotDecisionRow());
    expect(h.title).toBe("005930 삼성전자 매수");
    expect(decisionHeader(fixtureSpotDecisionRow({ name: null })).title).toBe("005930 매수");
    expect(h.chips).toEqual(["2026-11-05", "KR", "확신 72%", "session"]);
    expect(h.summaryLines).toEqual(["실적 상향 기대", "외국인 순매수 지속"]);
  });
  test("decisionHeader — fo 는 intent·direction·vehicle", () => {
    const h = decisionHeader(fixtureFoDecisionRow());
    expect(h.title).toBe("ENTER LONG FUTURES");
    expect(h.chips).toContain("filled");
    expect(h.summaryLines).toHaveLength(2);
  });
  test("roundRows", () => {
    const rows = roundRows(fixtureSpotDetail().rounds);
    expect(rows).toHaveLength(2);
    expect(rows[1]).toMatchObject({ round: 2, action: "TRIM", stop_loss: "66000", source: "reeval" });
  });
});

describe("결정 상세 화면", () => {
  const heads = (t: Element) => Array.from(t.querySelectorAll("thead th")).map((e) => e.textContent);
  const bodyRows = (t: Element) => t.querySelector("tbody")!.querySelectorAll("tr");

  test("실제 공급자와 독립 쟁점·상대 쟁점 반론을 읽을 수 있고 옛 기록에 공급자를 추정하지 않는다", async () => {
    const trace = fixtureSpotTrace({ debate: [
      { round: 1, side: "bear", stance: "BEARISH", conviction: 0.6, provider: "codex", model: "fixture-model", issues: [{ issue_id: "bear:1", topic: "금리 부담", claim: "자금조달 비용 상승", evidence: ["macro:us10y"] }], responses: [{ issue_id: "bull:1", response: "매출 증가와 이익 증가는 구분해야 함", evidence: ["news:3"] }] },
      { round: 0, side: "bull", stance: "BULLISH", conviction: 0.7 },
    ] });
    await mount({ ...spotRoutes, "GET /api/decisions/spot/7/trace": trace }, "/decisions/spot/7");
    const debate = section("debate")!;
    expect(debate.textContent).toContain("Codex / fixture-model");
    expect(debate.textContent).toContain("미기록");
    act(() => (debate.querySelector('button[aria-label="펼치기"]') as HTMLButtonElement).click());
    expect(debate.textContent).toContain("금리 부담");
    expect(debate.textContent).toContain("매출 증가와 이익 증가는 구분해야 함");
    expect(debate.textContent).toContain("bull:1");
  });

  test("spot 상세는 라운드·토론 턴·게이트·주문을 표로 그리고, 근거는 산문 그대로", async () => {
    await mount(spotRoutes, "/decisions/spot/7");
    expect(document.body.textContent).toContain("005930 삼성전자 매수");
    // 라운드 표
    const rounds = section("rounds")!.querySelector("table")!;
    expect(heads(rounds)).toEqual(["라운드", "판단", "손절", "출처", "시각"]);
    expect(bodyRows(rounds)).toHaveLength(2);
    expect(bodyRows(rounds)[0].textContent).toContain("보류");
    expect(bodyRows(rounds)[0].textContent).toContain("65,000");
    expect(section("rounds")!.querySelector(".decision-lines")).toBeNull();
    // 토론 턴 — details 안 표
    const debate = section("debate")!.querySelector("details table")!;
    expect(heads(debate)).toEqual(["라운드", "측", "참여 모델", "판단", "확신", "타이밍", "조건"]);
    expect(bodyRows(debate)).toHaveLength(3);
    expect(bodyRows(debate)[1].textContent).toContain("강세 측");
    expect(bodyRows(debate)[1].textContent).toContain("80%");
    expect(bodyRows(debate)[1].textContent).toContain("지금");
    // 타이밍이 객체({action, condition})인 턴 — 크래시 없이 action 라벨 + 조건
    expect(bodyRows(debate)[2].textContent).toContain("대기");
    expect(bodyRows(debate)[2].textContent).toContain("194,415 회복 뒤 진입");
    expect(section("analysts")!.textContent).toContain("매도");
    expect(section("analysts")!.textContent).not.toContain("SELL");
    // 근거 산문
    expect(section("rationale")!.textContent).toContain("외국인 순매수 지속");
    expect(section("rationale")!.textContent).toContain("승인 요약 — 합성 문단");
    // 게이트 표
    const gate = section("gate")!.querySelector("table")!;
    expect(heads(gate)).toEqual(["계좌", "결과", "수량", "사유", "비고"]);
    expect(bodyRows(gate)[0].textContent).toContain("paper-1");
    expect(gate.querySelector('.chip[data-tone="ok"]')!.textContent).toBe("통과");
    expect(bodyRows(gate)[0].textContent).toContain("한도 안");
    // 주문 표 — 결과 표는 중복이라 없고 /outcomes 도 부르지 않는다
    const orders = section("orders")!.querySelector("table")!;
    expect(heads(orders)).toEqual(["계좌", "방향", "수량", "가격", "상태", "주문번호", "메시지"]);
    expect(bodyRows(orders)[0].textContent).toContain("매수");
    expect(bodyRows(orders)[0].textContent).toContain("70,000");
    expect(bodyRows(orders)[0].querySelector("code")!.textContent).toBe("1");
    expect(Array.from(orders.querySelectorAll(".chip")).map((c) => c.textContent)).toEqual(["매수", "접수"]);
    expect(section("outcomes")).toBeNull();
    expect(ff!.calls.some((c) => c.url.includes("/outcomes"))).toBe(false);
    expect(section("analysts")!.textContent).toContain("강세");
  });

  test("US 결정은 손절·주문 가격을 센트까지(185.50·189.35) — 정수 반올림 금지", async () => {
    const us = { ...fixtureSpotDecisionRow(), market: "US", ticker: "AAPL", name: "Apple", limit_price: "189.35", stop_loss: "185.50", take_profit: "200.00" };
    const detail = fixtureSpotDetail({ decision: us, rounds: [{ id: 1, decision_id: 7, round: 1, action: "HOLD", stop_loss: "185.50", source: "reeval", skipped_debate: 0, data: {}, created_at: "2026-11-05T02:00:00+00:00" }] });
    const trace = fixtureSpotTrace({ decision: us });
    trace.orders[0].price = "189.35";
    await mount({ "GET /api/decisions/spot/7": detail, "GET /api/decisions/spot/7/trace": trace }, "/decisions/spot/7");
    const rounds = section("rounds")!.querySelector("table")!;
    expect(bodyRows(rounds)[0].textContent).toContain("185.50");
    expect(bodyRows(rounds)[0].textContent).not.toContain("186");
    const orders = section("orders")!.querySelector("table")!;
    expect(bodyRows(orders)[0].textContent).toContain("189.35"); // KR 정수(70,000)는 위 spot 테스트가 검증
  });

  test("「‹ 돌아가기」 와 복기 메모리 링크(ticker=005930)", async () => {
    const { container } = await mount(spotRoutes, "/decisions/spot/7");
    const links = Array.from(container.querySelectorAll("a"));
    expect(links.find((a) => a.textContent?.includes("‹ 돌아가기"))).toBeTruthy();
    const mem = links.find((a) => (a.getAttribute("href") ?? "").startsWith("/performance/memory?"));
    expect(mem!.getAttribute("href")).toBe("/performance/memory?ticker=005930");
  });

  test("체크포인트 칩을 누르면 그 단계 JSON 을 보인다", async () => {
    await mount(
      { ...spotRoutes, "GET /api/decisions/spot/7/checkpoints/30_decision": { stage: "30_decision", schema_version: 1, data: { action: "BUY" }, call: null } },
      "/decisions/spot/7",
    );
    const btn = Array.from(section("checkpoints")!.querySelectorAll("button")).find((b) => b.textContent === "30_decision")!;
    act(() => btn.click());
    await flush();
    expect(ff!.calls.map((c) => c.url)).toContain("/api/decisions/spot/7/checkpoints/30_decision");
    expect(section("checkpoints")!.querySelector("pre")!.textContent).toContain('"action": "BUY"');
  });

  test("에이전트 보고·토론 턴이 비면 절을 숨긴다", async () => {
    await mount({ ...spotRoutes, "GET /api/decisions/spot/7/trace": fixtureSpotTrace({ analysts: [], debate: [] }) }, "/decisions/spot/7");
    expect(section("analysts")!.hidden).toBe(true);
    expect(section("debate")!.hidden).toBe(true);
  });

  test("fo 상세는 라운드·토론·결과 절이 없다", async () => {
    await mount(foRoutes, "/decisions/fo/3");
    expect(document.body.textContent).toContain("ENTER LONG FUTURES");
    expect(section("rounds")).toBeNull();
    expect(section("debate")).toBeNull();
    expect(section("outcomes")).toBeNull();
    expect(section("analysts")!.textContent).toContain("macro");
    const checks = section("checks")!.querySelector("table")!;
    expect(heads(checks)).toEqual(["단계", "결과", "사유", "오류"]);
    expect(bodyRows(checks)[0].textContent).toContain("_fo_risk_check_paper-fo");
    expect(checks.querySelector('.chip[data-tone="ok"]')!.textContent).toBe("통과");
    expect(ff!.calls.some((c) => c.url.includes("/outcomes"))).toBe(false);
  });

  test("404 면 화면 전체가 「결정 없음」 하나", async () => {
    const { container } = await mount({}, "/decisions/spot/99");
    expect(container.querySelectorAll(".empty")).toHaveLength(1);
    expect(container.textContent).toContain("결정 없음");
    expect(section("rounds")).toBeNull();
  });
});
