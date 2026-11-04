// 옛 `web/src/usage.test.ts` 이식
import { describe, expect, it } from "vitest";
import { resetsIn, usageChip, usageLevel } from "../usage";

describe("usageLevel", () => {
  it("usageLevel(69.9)=ok, 70=warn, 90=fail", () => {
    expect(usageLevel(0)).toBe("ok");
    expect(usageLevel(69.9)).toBe("ok");
    expect(usageLevel(70)).toBe("warn");
    expect(usageLevel(89.9)).toBe("warn");
    expect(usageLevel(90)).toBe("fail");
    expect(usageLevel(140)).toBe("fail");
  });
});

describe("resetsIn", () => {
  const now = new Date("2026-10-23T03:00:00Z");
  it("분·시간 분·일", () => {
    expect(resetsIn("2026-10-23T03:30:00Z", now)).toBe("30분");
    expect(resetsIn("2026-10-23T04:20:00Z", now)).toBe("1시간 20분");
    expect(resetsIn("2026-10-23T05:10:00Z", now)).toBe("2시간 10분");
    expect(resetsIn("2026-10-27T03:00:00Z", now)).toBe("4일");
  });
  it("남은 1분 미만은 올림(0분으로 보이지 않게)", () => {
    expect(resetsIn("2026-10-23T03:00:20Z", now)).toBe("1분");
  });
  it("지났거나 모르면 빈 문자열", () => {
    expect(resetsIn("2026-10-23T02:00:00Z", now)).toBe("");
    expect(resetsIn(null, now)).toBe("");
    expect(resetsIn("nope", now)).toBe("");
  });
});

describe("usageChip", () => {
  const now = new Date("2026-11-05T01:00:00Z");
  it("글귀 '72% · 리셋 1시간 20분'", () => {
    expect(usageChip({ used_pct: 72, resets_at: "2026-11-05T02:20:00Z" }, now)).toEqual({
      text: "72% · 리셋 1시간 20분",
      level: "warn",
    });
  });
  it("사용률은 반올림, 리셋 모르면 사용률만", () => {
    expect(usageChip({ used_pct: 12.4, resets_at: null }, now)).toEqual({ text: "12%", level: "ok" });
    expect(usageChip({ used_pct: 95, resets_at: "2026-11-05T00:00:00Z" }, now)).toEqual({ text: "95%", level: "fail" });
  });
  it("관측 없는 창은 0% 가 아니라 —", () => {
    expect(usageChip(null, now)).toEqual({ text: "—", level: "none" });
    expect(usageChip({ used_pct: null, resets_at: null }, now)).toEqual({ text: "—", level: "none" });
  });
});
