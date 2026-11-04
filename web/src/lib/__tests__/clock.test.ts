// 옛 `web/src/clock.test.ts` 이식
import { afterEach, describe, expect, it } from "vitest";
import { clockSkewText, clockText, marketPhase, noteServerTime, now, serverOffsetMs } from "../clock";

afterEach(() => noteServerTime(new Date(0).toISOString(), 0));

describe("서버 시각 오프셋", () => {
  it("마지막 관측 1개만 쓴다(평활 없음)", () => {
    noteServerTime("2026-11-05T01:00:05.000Z", Date.parse("2026-11-05T01:00:00.000Z"));
    expect(serverOffsetMs()).toBe(5000);
    noteServerTime("2026-11-05T01:00:00.000Z", Date.parse("2026-11-05T01:00:02.000Z"));
    expect(serverOffsetMs()).toBe(-2000);
  });
  it("깨진 시각은 무시한다", () => {
    noteServerTime("2026-11-05T01:00:05.000Z", Date.parse("2026-11-05T01:00:00.000Z"));
    noteServerTime("not-a-date");
    expect(serverOffsetMs()).toBe(5000);
  });
  it("now() 는 오프셋을 적용한다", () => {
    noteServerTime(new Date(Date.now() + 60_000).toISOString());
    expect(Math.abs(now().getTime() - (Date.now() + 60_000))).toBeLessThan(500);
  });
});

describe("clockSkewText", () => {
  it("999ms 는 null, 1500ms 는 '시계 어긋남 +2초'", () => {
    expect(clockSkewText(999)).toBeNull();
    expect(clockSkewText(-999)).toBeNull();
    expect(clockSkewText(0)).toBeNull();
    expect(clockSkewText(1000)).toBe("시계 어긋남 +1초");
    expect(clockSkewText(1500)).toBe("시계 어긋남 +2초");
    expect(clockSkewText(-3200)).toBe("시계 어긋남 −3초");
  });
});

describe("marketPhase", () => {
  it("2026-11-05T10:00+09:00 은 kr 정규, us 장외", () => {
    expect(marketPhase(new Date("2026-11-05T10:00:00+09:00"))).toEqual({ kr: "정규", us: "장외", fo: "정규" });
  });
  it("미국 정규장(ET 09:30~16:00)", () => {
    // 2026-11-05 23:30 KST = 09:30 EST(서머타임 11-01 종료)
    expect(marketPhase(new Date("2026-11-05T23:30:00+09:00")).us).toBe("정규");
    expect(marketPhase(new Date("2026-11-05T23:29:00+09:00")).us).toBe("장외");
  });
  it("국내 장 경계·파생 08:45~15:45", () => {
    const at = (hm: string) => marketPhase(new Date(`2026-11-05T${hm}:00+09:00`));
    expect(at("08:50")).toMatchObject({ kr: "장외", fo: "정규" });
    expect(at("15:30")).toMatchObject({ kr: "장외", fo: "정규" });
    expect(at("15:45")).toMatchObject({ kr: "장외", fo: "장외" });
  });
  it("주말은 kr 휴장, us·fo 장외", () => {
    expect(marketPhase(new Date("2026-11-07T10:00:00+09:00"))).toEqual({ kr: "휴장", us: "장외", fo: "장외" });
  });
});

describe("clockText", () => {
  it("KST 시각·날짜·뉴욕 시각·장 상태", () => {
    const t = clockText(new Date("2026-10-26T12:06:05Z")); // 21:06:05 KST 월 · 08:06 ET
    expect(t.kst).toBe("21:06:05");
    expect(t.et).toBe("08:06:05");
    expect(t.date).toBe("10.26 (월)");
    expect(t.etDate).toBe("10.26 (월)");
    expect(t.phase).toBe("KR 장외 · US 장외 · 파생 장외");
  });

  it("뉴욕 날짜는 뉴욕 기준(한국 새벽이면 하루 전)", () => {
    const t = clockText(new Date("2026-10-26T16:30:00Z")); // 10.27(화) 01:30 KST · 10.26(월) 12:30 ET
    expect(t.date).toBe("10.27 (화)");
    expect(t.etDate).toBe("10.26 (월)");
    expect(t.et).toBe("12:30:00");
    expect(t.phase).toBe("KR 장외 · US 정규 · 파생 장외");
  });
});
