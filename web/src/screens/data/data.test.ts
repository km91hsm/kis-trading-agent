import { describe, expect, test } from "vitest";
import { fixtureFeaturesLatest, fixtureLabels, fixtureNames, fixtureNews, fixtureRefs, fixtureThemes } from "../../test/fixtures/data";
import { chartLines, chartMarkers, featureRows, fmt, fmtFeature, KEY_COLUMNS, newsRows, sortRows, themeRows } from "./data.ts";

describe("newsRows", () => {
  test("종목 칩은 이름을 쓰고, 촉매·테마·「판정에 쓰임」 칩과 첫 판정 id 를 단다", () => {
    const [a, b] = newsRows(fixtureNews().items, fixtureLabels(), fixtureRefs(), fixtureNames());
    expect(a).toMatchObject({ id: 101, title: "삼성전자 HBM 공급 확대", source: "naver", at: "2026-11-05T00:30:00+00:00" });
    expect(a.tickers).toEqual([{ ticker: "005930", name: "삼성전자" }]);
    expect(a.chips).toEqual(["촉매", "반도체", "판정에 쓰임"]);
    expect(a.decisionId).toBe(7);
    // 라벨·refs 없음 → 칩 없음, 이름 모르면 종목 코드, published_at 없으면 received_at
    expect(b.chips).toEqual([]);
    expect(b.decisionId).toBeNull();
    expect(b.tickers).toEqual([{ ticker: "000660", name: "000660" }]);
    expect(b.at).toBe("2026-11-05T00:31:00+00:00");
  });
});

describe("themeRows", () => {
  test("종목 이름·근거, 구성 수, 촉매 확인", () => {
    expect(themeRows(fixtureThemes())).toEqual([
      { theme: "반도체", score: 0.8123, tickers: [{ ticker: "005930", name: "삼성전자", why: ["뉴스 3건", "거래량 급증"] }], members: 2, catalystOk: true },
    ]);
  });
});

describe("featureRows·sortRows·fmt (옛 pages/features.ts 이식)", () => {
  test("KEY_COLUMNS 만 남기고 정렬, null 은 방향과 무관하게 마지막", () => {
    const desc = featureRows(fixtureFeaturesLatest(), "roe", "desc");
    expect(desc.map((r) => r.ticker)).toEqual(["000660", "005930", "035420"]);
    expect(Object.keys(desc[0])).toEqual(KEY_COLUMNS);
    expect(featureRows(fixtureFeaturesLatest(), "roe", "asc").map((r) => r.ticker)).toEqual(["005930", "000660", "035420"]);
    expect(featureRows(fixtureFeaturesLatest(), null, "asc").map((r) => r.ticker)).toEqual(["005930", "000660", "035420"]);
  });
  test("sortRows 는 원본을 바꾸지 않는다, fmt 는 소수 4자리·null 은 —", () => {
    const rows = [{ ticker: "A" }, { ticker: "C" }, { ticker: "B" }];
    expect(sortRows(rows, "ticker", "desc").map((r) => r.ticker)).toEqual(["C", "B", "A"]);
    expect(rows.map((r) => r.ticker)).toEqual(["A", "C", "B"]);
    expect(fmt(0.123456)).toBe("0.1235");
    expect(fmt(71500)).toBe("71500");
    expect(fmt(null)).toBe("—");
    expect(fmt("KR")).toBe("KR");
  });
});

describe("chartMarkers·chartLines", () => {
  test("체결 표식은 매수 --up·매도 --down 색 이름", () => {
    expect(
      chartMarkers([
        { time: "2026-11-05", kind: "fill", side: "buy", text: "매수 10" },
        { time: "2026-11-06", kind: "fill", side: "SELL", text: "매도 10" },
      ]),
    ).toEqual([
      { time: "2026-11-05", text: "매수 10", color: "--up" },
      { time: "2026-11-06", text: "매도 10", color: "--down" },
    ]);
  });

  test("해당 종목 보유 행의 손절·목표 2선(없는 값은 건너뜀)", () => {
    const positions = [
      { ticker: "000660", stop: { level: "1", source: "x" }, target: null },
      { ticker: "005930", stop: { level: "65000", source: "atr" }, target: { level: 80000, source: "rr" } },
    ];
    const lines = chartLines(positions, "005930");
    expect(lines).toHaveLength(2);
    expect(lines).toEqual([
      { price: 65000, label: "손절", color: "--fail" },
      { price: 80000, label: "목표", color: "--ok" },
    ]);
    expect(chartLines([{ ticker: "005930", stop: { level: null, source: "atr" }, target: null }], "005930")).toEqual([]);
    expect(chartLines(positions, "AAPL")).toEqual([]);
  });
});

describe("fmtFeature", () => {
  test("종가는 US 소수(189.35)를 살리고 KR 정수(70,500)는 천 단위, 거래량은 정수, 비율 열은 %", () => {
    expect(fmtFeature("close", 189.35)).toBe("189.35");
    expect(fmtFeature("close", 70500)).toBe("70,500");
    expect(fmtFeature("volume", 1234567.0)).toBe("1,234,567");
    expect(fmtFeature("roe", 0.1234)).toBe("12.34%");
    expect(fmtFeature("close", null)).toBe("—");
  });
});
