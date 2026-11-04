// 옛 `web/src/format.test.ts` 이식
import { describe, expect, it } from "vitest";
import { agoText, fmtDate, fmtKrw, fmtMoney, fmtPct, fmtPt, fmtTime, fmtTimeEt, fmtUsd, kstDate } from "../format";

describe("금액", () => {
  it("fmtKrw('1234567.00') === '1,234,567'; fmtUsd('12.5') === '12.50'; fmtPt('312.25')", () => {
    expect(fmtKrw("1234567.00")).toBe("1,234,567");
    expect(fmtUsd("12.5")).toBe("12.50");
    expect(fmtPt("312.25")).toBe("312.25");
  });
  it("원화는 정수로 반올림, 음수 부호", () => {
    expect(fmtKrw(2580000.4)).toBe("2,580,000");
    expect(fmtKrw("-312400")).toBe("-312,400");
    expect(fmtKrw("-0.4")).toBe("0");
  });
  it("달러·포인트는 소수 둘째 + 천 단위", () => {
    expect(fmtUsd(1234.5)).toBe("1,234.50");
    expect(fmtUsd("-1.4")).toBe("-1.40");
    expect(fmtPt(1312.256)).toBe("1,312.26");
  });
  it("null·빈 값·숫자 아님은 —", () => {
    expect(fmtKrw(null)).toBe("—");
    expect(fmtUsd("")).toBe("—");
    expect(fmtPt("abc")).toBe("—");
    expect(fmtMoney(null, "USD")).toBe("—");
  });
  it("fmtMoney 는 통화별로 고른다", () => {
    expect(fmtMoney("2580000", "KRW")).toBe("2,580,000");
    expect(fmtMoney("231.8", "USD")).toBe("231.80");
  });
});

describe("fmtPct", () => {
  it("비율 → 소수 자리 + %", () => {
    expect(fmtPct(0.0153)).toBe("1.53%");
    expect(fmtPct(-0.019, 1)).toBe("-1.9%");
    expect(fmtPct("0.5", 0)).toBe("50%");
    expect(fmtPct(null)).toBe("—");
  });
});

describe("시각·날짜", () => {
  it("fmtTime 은 KST HH:MM:SS", () => {
    expect(fmtTime("2026-10-23T00:31:05+00:00")).toBe("09:31:05");
    expect(fmtTime("2026-10-23T15:00:00Z")).toBe("00:00:00");
    expect(fmtTime(null)).toBe("—");
    expect(fmtTime("nope")).toBe("—");
  });
  it("fmtTimeEt 은 'HH:MM ET'", () => {
    expect(fmtTimeEt("2026-10-23T00:31:00+00:00")).toBe("20:31 ET"); // EDT
    expect(fmtTimeEt("2026-11-05T14:30:00Z")).toBe("09:30 ET"); // EST
    expect(fmtTimeEt(null)).toBe("—");
  });
  it("fmtDate 는 KST YYYY-MM-DD, 날짜 문자열은 그대로", () => {
    expect(fmtDate("2026-10-22T16:30:00+00:00")).toBe("2026-10-23");
    expect(fmtDate("2026-10-23")).toBe("2026-10-23");
    expect(fmtDate(null)).toBe("—");
  });
  it("kstDate 는 KST 오늘", () => {
    expect(kstDate(new Date("2026-11-04T15:00:00Z"))).toBe("2026-11-05");
    expect(kstDate(new Date("2026-11-04T14:59:59Z"))).toBe("2026-11-04");
  });
});

describe("agoText", () => {
  const now = new Date("2026-11-05T01:00:00Z");
  const before = (s: number) => new Date(now.getTime() - s * 1000).toISOString();
  it("agoText(30초 전) === '30초 전', 90초 전 === '1분 전'", () => {
    expect(agoText(before(30), now)).toBe("30초 전");
    expect(agoText(before(90), now)).toBe("1분 전");
  });
  it("경계와 큰 값", () => {
    expect(agoText(before(0), now)).toBe("0초 전");
    expect(agoText(before(59), now)).toBe("59초 전");
    expect(agoText(before(60), now)).toBe("1분 전");
    expect(agoText(before(3599), now)).toBe("59분 전");
    expect(agoText(before(7200), now)).toBe("2시간 전");
    expect(agoText(before(3 * 86400), now)).toBe("3일 전");
  });
  it("미래(시계 어긋남)는 0초, 없거나 깨지면 —", () => {
    expect(agoText(new Date(now.getTime() + 5000).toISOString(), now)).toBe("0초 전");
    expect(agoText(null, now)).toBe("—");
    expect(agoText("nope", now)).toBe("—");
  });
});
