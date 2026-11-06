import { describe, expect, test } from "vitest";
import { cardRows, resumeNeedsConfirm, totals } from "./accounts.ts";
import { fixtureAccounts, fixtureOverview } from "../../test/fixtures/accounts";

describe("accounts 순수 함수", () => {
  test("totals: fx null → fxLine '환율 없음', converted null; fx 있음 → '적용 환율 USD/KRW 1,385.50 (11월 4일 종가) → 환산 합계 …'", () => {
    const none = totals(fixtureOverview({ fx: null }));
    expect(none.fxLine).toBe("환율 없음");
    expect(none.converted).toBeNull();

    const t = totals(fixtureOverview());
    expect(t.krw).toBe("13,000,000원");
    expect(t.krwScope).toBe("계좌 2");
    expect(totals(fixtureOverview(), fixtureAccounts()).krwScope).toBe("모의 1 · 실전 1");
    expect(totals(fixtureOverview(), fixtureAccounts()).usdScope).toBe("모의 1");
    expect(t.krwPnl).toBe("+75,000원");
    expect(t.usd).toBe("$2,500.50");
    expect(t.usdPnl).toBe("-$12.25");
    expect(t.fxLine).toBe("적용 환율 USD/KRW 1,385.50 (11월 4일 종가)");
    expect(t.converted).toBe("환산 합계 16,464,443원");
    expect(`${t.fxLine} → ${t.converted}`).toBe("적용 환율 USD/KRW 1,385.50 (11월 4일 종가) → 환산 합계 16,464,443원");

    const iso = totals(fixtureOverview({ fx: { pair: "USD/KRW", rate: "1385.5", as_of: "2026-11-04T07:00:00+00:00", converted_total: "1" } }));
    expect(iso.fxLine).toBe("적용 환율 USD/KRW 1,385.50 (2026-11-04)");
  });

  test("resumeNeedsConfirm: real-1 auto 면 'real-1', 모의만이면 null", () => {
    const accts = fixtureAccounts();
    expect(resumeNeedsConfirm(accts)).toBe("real-1");
    expect(resumeNeedsConfirm(accts.filter((a) => a.env === "paper"))).toBeNull();
    expect(resumeNeedsConfirm(accts.map((a) => ({ ...a, mode: "approve" })))).toBeNull();
  });

  test("cardRows: 레인·모의/실전·모드·NAV·당일 손익·보유 수·대기 승인", () => {
    const rows = cardRows(fixtureAccounts());
    expect(rows.map((r) => [r.id, r.href, r.lane, r.envLabel, r.live])).toEqual([
      ["paper-1", "/accounts/paper-1", "현물", "모의", false],
      ["real-1", "/accounts/real-1", "현물", "실전", true],
      ["paper-fo", "/accounts/paper-fo", "파생", "모의", false],
    ]);
    const real = rows[1];
    expect(real.mode).toBe("자동");
    expect(real.modeNote).toBe("LIVE 확인 11-01");
    expect(real.nav).toBe("3,000,000원");
    expect(real.dayPnl).toBe("-45,000원");
    expect(real.positions).toBe(1);
    expect(real.pending).toBe(2);
    expect(rows[0].dayPnl).toBe("+120,000원");
    expect(rows[0].mode).toBe("승인");
    expect(rows[2].mode).toBe("끔");
    expect(rows[2].dayPnl).toBe("0원");
  });
});
