import { describe, expect, it } from "vitest";
import { healthDot, healthSummary, type Health } from "../health";

const h = (level: Health["level"], reasons: Health["reasons"]): Health => ({
  level,
  reasons,
  checked_at: "2026-11-05T01:00:00+00:00",
});

describe("healthDot", () => {
  it("ok·warn·fail 은 그대로, 그 밖은 warn", () => {
    expect(healthDot("ok")).toBe("ok");
    expect(healthDot("warn")).toBe("warn");
    expect(healthDot("fail")).toBe("fail");
    expect(healthDot("info")).toBe("warn");
    expect(healthDot(null)).toBe("warn");
  });
});

describe("healthSummary", () => {
  it("healthSummary(fail 데몬 멈춤, skew) === '데몬 멈춤 · 시계 어긋남 +2초'", () => {
    expect(healthSummary(h("fail", [{ area: "daemon", level: "fail", text: "데몬 멈춤" }]), "시계 어긋남 +2초")).toBe(
      "데몬 멈춤 · 시계 어긋남 +2초",
    );
  });
  it("전체 단계와 같은 사유를 고르고, 여러 줄이면 첫 줄만", () => {
    const x = h("fail", [
      { area: "control", level: "info", text: "paused" },
      { area: "runs", level: "warn", text: "오류 연속" },
      { area: "daemon", level: "fail", text: "데몬이 꺼져 있어요\n마지막 하트비트 10:00" },
    ]);
    expect(healthSummary(x, null)).toBe("데몬이 꺼져 있어요");
  });
  it("정상이면 '정상'(info 사유는 띄우지 않음), 응답 전이면 '상태 확인 중'", () => {
    expect(healthSummary(h("ok", [{ area: "control", level: "info", text: "paused" }]), null)).toBe("정상");
    expect(healthSummary(h("ok", []), "시계 어긋남 −3초")).toBe("정상 · 시계 어긋남 −3초");
    expect(healthSummary(null, null)).toBe("상태 확인 중");
  });
});
