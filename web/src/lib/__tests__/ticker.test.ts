import { describe, expect, test } from "vitest";
import { tickerLabel } from "../ticker";

describe("tickerLabel", () => {
  test("이름이 있으면 「코드 이름」, 없으면 코드만, 코드도 없으면 —", () => {
    expect(tickerLabel("005930", "삼성전자")).toBe("005930 삼성전자");
    expect(tickerLabel("AAPL", null)).toBe("AAPL");
    expect(tickerLabel("AAPL", undefined)).toBe("AAPL");
    expect(tickerLabel(null, null)).toBe("—");
    expect(tickerLabel("005930", "")).toBe("005930");
  });
});
