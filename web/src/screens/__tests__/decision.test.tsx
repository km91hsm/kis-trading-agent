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
    expect(h.title).toBe("「005930 매수」");
    expect(h.chips).toEqual(["2026-11-05", "KR", "확신 72%", "session"]);
    expect(h.summaryLines).toEqual(["실적 상향 기대", "외국인 순매수 지속"]);
  });
  test("decisionHeader — fo 는 intent·direction·vehicle", () => {
    const h = decisionHeader(fixtureFoDecisionRow());
    expect(h.title).toBe("「ENTER LONG FUTURES」");
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
  test("spot 상세는 라운드 2개·토론 턴 3개를 그린다", async () => {
    await mount(spotRoutes, "/decisions/spot/7");
    expect(document.body.textContent).toContain("「005930 매수」");
    expect(section("rounds")!.querySelectorAll("li.round-row")).toHaveLength(2);
    expect(section("debate")!.querySelectorAll("li.debate-turn")).toHaveLength(3);
    expect(section("rationale")!.textContent).toContain("외국인 순매수 지속");
    expect(section("rationale")!.textContent).toContain("승인 요약 — 합성 문단");
    expect(section("gate")!.textContent).toContain("paper-1: 통과 3주 · 한도 안");
    expect(section("orders")!.textContent).toContain("paper-1: 매수 3주 @70000 → 접수 #1");
    expect(section("orders")!.textContent).toContain("체결");
    expect(section("analysts")!.textContent).toContain("강세");
  });

  test("「‹ 돌아가기」 와 복기 메모리 링크(ticker=005930)", async () => {
    const { container } = await mount(spotRoutes, "/decisions/spot/7");
    const links = Array.from(container.querySelectorAll("a"));
    expect(links.find((a) => a.textContent?.includes("‹ 돌아가기"))).toBeTruthy();
    const mem = links.find((a) => (a.getAttribute("href") ?? "").startsWith("/performance#memory"));
    expect(mem!.getAttribute("href")).toContain("ticker=005930");
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

  test("fo 상세는 라운드·토론·결과 절이 없다", async () => {
    await mount(foRoutes, "/decisions/fo/3");
    expect(document.body.textContent).toContain("「ENTER LONG FUTURES」");
    expect(section("rounds")).toBeNull();
    expect(section("debate")).toBeNull();
    expect(section("outcomes")).toBeNull();
    expect(section("analysts")!.textContent).toContain("macro");
    expect(section("checks")!.textContent).toContain("_fo_risk_check_paper-fo");
    expect(ff!.calls.some((c) => c.url.includes("/outcomes"))).toBe(false);
  });

  test("404 면 화면 전체가 「결정 없음」 하나", async () => {
    const { container } = await mount({}, "/decisions/spot/99");
    expect(container.querySelectorAll(".empty")).toHaveLength(1);
    expect(container.textContent).toContain("결정 없음");
    expect(section("rounds")).toBeNull();
  });
});
