import { describe, expect, test } from "vitest";
import { fixtureCalibration, fixtureCompare, fixtureMemory, fixturePerfAccounts, fixturePerformance, fixtureShadow } from "../../test/fixtures/performance";
import { benchmarkLines, calibrationBins, normalizeNav, compareRows, defaultFilters, memoryRows, shadowRows, type Compare, type Calibration, type MemoryResp, type Perf, type Shadow } from "./performance.ts";

describe("성과 순수 함수", () => {
  test("defaultFilters: 첫 계좌·그 첫 시장·30일, 계좌 없으면 빈 값", () => {
    expect(defaultFilters(fixturePerfAccounts())).toEqual({ account_id: "paper-1", market: "KR", days: "30" });
    expect(defaultFilters([])).toEqual({ account_id: "", market: "", days: "30" });
  });

  test("compareRows: metrics null 이면 「NAV 없음」 과 「—」", () => {
    const rows = compareRows(fixtureCompare() as Compare);
    expect(rows[0]).toMatchObject({ key: "paper-1-KR", account_id: "paper-1", market: "KR", ret: "5.23%", mdd: "-8.10%", sortino: "1.23", note: "" });
    expect(rows[1]).toMatchObject({ key: "paper-1-US", market: "US", currency: "USD", ret: "—", mdd: "—", sortino: "—", note: "NAV 없음" });
  });

  test("calibrationBins: run×역할 행, report null 이면 「표본 부족」 한 줄", () => {
    const rows = calibrationBins(fixtureCalibration() as Calibration);
    expect(rows.map((r) => [r.run_id, r.role])).toEqual([
      [12, "bull"],
      [12, "bear"],
      [11, "—"],
    ]);
    expect(rows[0]).toMatchObject({ active: true, hit_rate: "60.00%", n: 20, neighbor_hit_rate: "55.00%", bias: "+0.12", note: "" });
    expect(rows[1]).toMatchObject({ neighbor_hit_rate: "—", bias: "-0.05" });
    expect(rows[2]).toMatchObject({ active: false, hit_rate: "—", note: "표본 부족" });
  });

  test("memoryRows: 결정 action·결과 수익률(없으면 —)·요약", () => {
    const rows = memoryRows(fixtureMemory() as MemoryResp);
    expect(rows[0]).toMatchObject({ id: 1, ticker: "005930", market: "KR", action: "buy", outcome: "3.40%", tier: "short", importance: 0.7, summary: "반도체 업황 반등 기대" });
    expect(rows[1]).toMatchObject({ ticker: "AAPL", action: "—", outcome: "—", summary: "" });
  });

  test("benchmarkLines: null·빈 시리즈는 건너뛴다", () => {
    const lines = benchmarkLines(fixturePerformance() as Perf);
    expect(lines.map((l) => l.label)).toEqual(["1/N 균등", "매수 후 보유"]);
    expect(lines[0].series).toEqual([{ time: "2026-11-03", value: 1 }, { time: "2026-11-05", value: 1.031 }]);
    const empty = fixturePerformance({ benchmarks: { one_over_n: { return: null, series: [] }, buy_and_hold: { return: null, series: [] }, quant: null, benchmark_universe: [] } });
    expect(benchmarkLines(empty as Perf)).toEqual([]);
  });

  test("shadowRows: 기준선별 n·평균차·CI, min_pairs 미만이면 표본 부족 메모", () => {
    const rows = shadowRows(fixtureShadow() as Shadow);
    expect(rows[0]).toMatchObject({ key: "candidates_1n", label: "같은 후보 균등(1/N)", n: 25, mean: "0.40%", ci: "0.10% ~ 0.70%", note: "" });
    expect(rows[1]).toMatchObject({ key: "quant_topn", n: 5, ci: "—", note: "표본 부족(n < 20)" });
  });

  test("normalizeNav: 첫 값 기준 1.0, 첫 값 0·빈 입력은 빈 배열", () => {
    expect(normalizeNav([{ time: "2026-11-04", value: 10_000_000 }, { time: "2026-11-05", value: 10_500_000 }]).map((p) => p.value)).toEqual([1, 1.05]);
    expect(normalizeNav([])).toEqual([]);
    expect(normalizeNav([{ time: "2026-11-04", value: 0 }, { time: "2026-11-05", value: 5 }])).toEqual([]);
  });
});
