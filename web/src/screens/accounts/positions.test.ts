import { describe, expect, test } from "vitest";
import { fixtureFoReport, fixturePerformance, fixturePositions } from "../../test/fixtures/accounts";
import { costBasis, marketValue, modeOptions, navSeries, splitLanes, stopLabel, sumEval, withLive, type PositionRow } from "./positions.ts";

const rows = () => fixturePositions().items as PositionRow[];
const quote = (price: string, at: string, over: Record<string, unknown> = {}) => ({
  session: "open",
  at,
  quotes: { "005930": { price, change_pct: 0.01, at, stale: false, source: "ws" as const, ...over } },
  source: "ws" as const,
  next_poll_ms: 3000,
});

describe("계좌별 화면 순수 함수", () => {
  test("splitLanes: scalp 행은 단타 절로, sumEval 은 단타 제외", () => {
    const lanes = splitLanes(rows());
    expect(lanes.spot.map((r) => r.ticker)).toEqual(["005930", "AAPL"]);
    expect(lanes.scalp.map((r) => r.ticker)).toEqual(["000660"]);
    expect(lanes.fo).toEqual([]);
    // KRW 행만: 005930 10주 × 서버 현재가 71,000(평단 70,000) — 단타 000660 은 빠진다
    const krw = rows().filter((r) => r.currency === "KRW");
    expect(sumEval(krw)).toEqual({ cost: "700,000", eval: "710,000", pnl: "+10,000" });
    expect(sumEval(withLive(krw, null, 0))).toEqual({ cost: "700,000", eval: "710,000", pnl: "+10,000" });
    // 현재가가 아예 없으면 평단으로(손익 0)
    expect(sumEval(krw.map((r) => ({ ...r, current_price: null })))).toEqual({ cost: "700,000", eval: "700,000", pnl: "0" });
    const live = withLive(krw, quote("72000", "2026-11-05T01:00:00Z"), Date.parse("2026-11-05T01:00:05Z"));
    expect(sumEval(live)).toEqual({ cost: "700,000", eval: "720,000", pnl: "+20,000" });
  });

  test("stopLabel({level:'70000', source:'추적'}) === '70,000 · 추적'", () => {
    expect(stopLabel({ level: "70000", source: "추적" })).toBe("70,000 · 추적");
    expect(stopLabel({ level: "195.5", source: "기본 −7%" }, "USD")).toBe("195.50 · 기본 −7%");
    expect(stopLabel({ level: "345.5", source: "손절 −1.5%" }, "PT")).toBe("345.50 · 손절 −1.5%");
    expect(stopLabel(null)).toBe("—");
  });

  test("withLive: 새 시세면 live, 시세 없거나 낡으면 서버 current_price 유지(live false, 낡으면 stale)", () => {
    const at = "2026-11-05T01:00:00Z";
    const fresh = withLive(rows(), quote("72000", at), Date.parse(at) + 1000);
    expect(fresh[0]).toMatchObject({ current_price: 72000, pnl: 20000, live: true, stale: false });
    expect(fresh[1]).toMatchObject({ current_price: 210, pnl: 19, live: false, stale: false });
    const old = withLive(rows(), quote("72000", at), Date.parse(at) + 60_000);
    expect(old[0]).toMatchObject({ current_price: 71000, pnl: 10000, live: false, stale: true });
    expect(withLive(rows(), null, 0)[0]).toMatchObject({ current_price: 71000, pnl: 10000, pnl_pct: (10000 / 700000) * 100, live: false, stale: false });
    // 서버 현재가도 없으면 낡은 시세 가격을 보인다(stale)
    const bare = rows().map((r) => ({ ...r, current_price: null }));
    expect(withLive(bare, quote("72000", at), Date.parse(at) + 60_000)[0]).toMatchObject({ current_price: 72000, live: false, stale: true });
    expect(withLive(bare, null, 0)[0]).toMatchObject({ current_price: null, pnl: null });
  });

  test("navSeries: 현물 {time,value}·파생 {date,nav} 모두 {time, value:number}", () => {
    expect(navSeries(fixturePerformance())).toEqual([
      { time: "2026-11-03", value: 9950000 },
      { time: "2026-11-04", value: 10000000 },
    ]);
    expect(navSeries(fixtureFoReport())).toEqual([
      { time: "2026-11-03", value: 49800000 },
      { time: "2026-11-04", value: 50000000 },
    ]);
    expect(navSeries({ nav: [{ time: "2026-11-03", value: null }] })).toEqual([]);
  });

  test("modeOptions: 실전은 auto 를 맨 뒤(LIVE 동작)", () => {
    expect(modeOptions("paper")).toEqual(["auto", "approve", "off"]);
    expect(modeOptions("real")).toEqual(["approve", "off", "auto"]);
  });
});

describe("marketValue", () => {
  test("수량 × 현재가", () => {
    expect(marketValue({ qty: "10", current_price: "72000" } as PositionRow)).toBe(720000);
    expect(marketValue({ qty: 2, current_price: 210.5 } as PositionRow)).toBe(421);
  });

  test("수량이나 현재가를 모르면 null — 평단으로 대신 계산하지 않는다", () => {
    expect(marketValue({ qty: "10", current_price: null } as PositionRow)).toBeNull();
    expect(marketValue({ qty: null, current_price: "72000" } as PositionRow)).toBeNull();
    expect(marketValue({ qty: "10", avg_price: "70000" } as PositionRow)).toBeNull();
  });
});

describe("costBasis", () => {
  test("수량 × 평단", () => {
    expect(costBasis({ qty: "10", avg_price: "70000" } as PositionRow)).toBe(700000);
  });

  test("수량이나 평단을 모르면 null", () => {
    expect(costBasis({ qty: "10", avg_price: null } as PositionRow)).toBeNull();
    expect(costBasis({ qty: null, avg_price: "70000" } as PositionRow)).toBeNull();
  });
});

test("sumEval 은 총 매수금액도 돌려준다", () => {
  const held = [
    { lane: "spot", currency: "KRW", qty: "10", avg_price: "70000", current_price: "71000" },
    { lane: "spot", currency: "KRW", qty: "1", avg_price: "5000", current_price: "6000" },
    { lane: "scalp", currency: "KRW", qty: "1", avg_price: "9000", current_price: "9000" },
  ] as PositionRow[];
  expect(sumEval(held).cost).toBe("705,000");
});

