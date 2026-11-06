import { describe, expect, test } from "vitest";
import { pagePathOf, resolveLink, screenPages } from "../nav";

describe("nav 페이지", () => {
  test("시스템 화면은 페이지 4개(상태·잡·기능·관리), 다른 화면은 자기 자신 하나", () => {
    expect(screenPages("system").map((p) => p.path)).toEqual(["/system", "/system/jobs", "/system/features", "/system/admin"]);
    expect(screenPages("today")).toEqual([{ id: "today", label: "오늘", path: "/" }]);
  });
  test("절 id → 그 절이 있는 페이지 경로", () => {
    expect(pagePathOf("system", "runs")).toBe("/system/jobs");
    expect(pagePathOf("system", "regime")).toBe("/system/features");
    expect(pagePathOf("system", "health")).toBe("/system");
    expect(pagePathOf("data", "news")).toBe("/data");
    expect(pagePathOf("system", "nope")).toBeNull();
  });
  test("백엔드 링크 /system#절 은 페이지 주소로 바꾼다(절 없는 링크·다른 화면은 그대로)", () => {
    expect(resolveLink("/system#runs")).toBe("/system/jobs#runs");
    expect(resolveLink("/system#control-log")).toBe("/system/admin#control-log");
    expect(resolveLink("/system")).toBe("/system");
    expect(resolveLink("/data")).toBe("/data");
    expect(resolveLink("/")).toBe("/");
    expect(resolveLink("https://x.test/a")).toBe("https://x.test/a");
  });
});
