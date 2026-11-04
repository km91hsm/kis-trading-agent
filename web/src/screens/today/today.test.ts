import { describe, expect, test } from "vitest";
import { decisionHref, decisionRows, laneCards, statusLabel } from "./today.ts";
import {
  fixtureAccount,
  fixtureFoDecision,
  fixtureScalpDecision,
  fixtureSpotDecision,
  fixtureSpotOrder,
} from "../../test/fixtures/today";

describe("today 순수 함수", () => {
  test("laneCards: fo 계좌 없으면 fo 카드 없음; spot 결정 매수2 매도1 보류1, 주문 3 체결 2 미체결 1", () => {
    const accounts = [fixtureAccount(), fixtureAccount({ id: "paper-2", lane: "scalp" })];
    const decisions = [
      fixtureSpotDecision({ id: 1, side: "BUY", action: "BUY" }),
      fixtureSpotDecision({ id: 2, side: "BUY", action: "ADD" }),
      fixtureSpotDecision({ id: 3, side: "SELL", action: "TRIM" }),
      fixtureSpotDecision({ id: 4, side: null, action: "HOLD", status: null }),
      fixtureScalpDecision(),
      fixtureFoDecision(), // 계좌 없는 레인 — 카드 없음
    ];
    const orders = [
      fixtureSpotOrder({ id: 1, status: "filled", filled_qty: 3 }),
      fixtureSpotOrder({ id: 2, status: "filled", filled_qty: 3 }),
      fixtureSpotOrder({ id: 3, status: "accepted", filled_qty: 0 }),
      fixtureSpotOrder({ id: 4, lane: "scalp", status: "cancelled", filled_qty: 0 }),
    ];
    const caps = [
      { kind: "analyze", used: 2, cap: 3 },
      { kind: "fo", used: 1, cap: 2 },
    ];
    const cards = laneCards(accounts, decisions, orders, caps);
    expect(cards.map((c) => c.lane)).toEqual(["spot", "scalp"]);
    const spot = cards[0];
    expect(spot.decisions).toEqual({ buy: 2, sell: 1, hold: 1 });
    expect([spot.orders, spot.fills, spot.open]).toEqual([3, 2, 1]);
    expect(spot.state).toBe("waiting");
    expect(spot.capText).toBe("오늘 analyze 2/3");
    const scalp = cards[1];
    expect(scalp.decisions).toEqual({ buy: 0, sell: 1, hold: 0 });
    expect([scalp.orders, scalp.fills, scalp.open]).toEqual([1, 0, 0]);
    expect(scalp.state).toBe("done");
    expect(scalp.capText).toBeNull();
  });

  test("laneCards: 일시정지 계좌면 paused, fo 는 intent 로 매수·매도를 센다, cap 없으면 null", () => {
    const accounts = [fixtureAccount({ lane: "fo", paused: true })];
    const decisions = [fixtureFoDecision({ intent: "OPEN" }), fixtureFoDecision({ id: 4, intent: "CLOSE" }), fixtureFoDecision({ id: 5, intent: "HOLD" })];
    const [fo] = laneCards(accounts, decisions, [], [{ kind: "fo", used: 1, cap: null }]);
    expect(fo.lane).toBe("fo");
    expect(fo.decisions).toEqual({ buy: 1, sell: 1, hold: 1 });
    expect(fo.state).toBe("paused");
    expect(fo.capText).toBeNull();
    const [fo2] = laneCards([fixtureAccount({ lane: "fo" })], [], [], [{ kind: "fo", used: 2, cap: 3 }]);
    expect(fo2.capText).toBe("오늘 fo 2/3");
    expect(fo2.state).toBe("done");
    const [spotWaiting] = laneCards([fixtureAccount({ pending_approvals: 1 })], [], [], []);
    expect(spotWaiting.state).toBe("waiting");
    const [part] = laneCards([fixtureAccount()], [], [fixtureSpotOrder({ status: "partial", filled_qty: 1 })], []);
    expect([part.orders, part.fills, part.open, part.state]).toEqual([1, 1, 1, "waiting"]);
  });

  test("decisionRows: 시간 역순, scalp-12 는 href null, spot 7 은 /decisions/spot/7", () => {
    const rows = decisionRows([
      fixtureSpotDecision({ id: 7, at: "2026-11-05T01:00:00+00:00", status: "pending_approval" }),
      fixtureScalpDecision({ id: "scalp-12", at: "2026-11-05T02:00:00+00:00", status: "filled" }),
      fixtureFoDecision({ id: 3, at: "2026-11-05T00:30:00+00:00", status: "expired" }),
    ]);
    expect(rows.map((r) => r.key)).toEqual(["spot-scalp-12", "spot-7", "fo-3"]);
    expect(rows[0].href).toBeNull();
    expect(rows[1].href).toBe("/decisions/spot/7");
    expect(rows[2].href).toBe("/decisions/fo/3");
    expect(rows.map((r) => r.status)).toEqual(["체결", "대기", "만료"]);
    expect(decisionHref({ asset: "spot", id: "scalp-12", lane: "scalp" })).toBeNull();
    expect(decisionHref({ asset: "spot", id: 7, lane: "spot" })).toBe("/decisions/spot/7");
  });

  test("statusLabel: 대기/승인/주문/체결/거절/만료, 모르는 값은 원문, 없으면 —", () => {
    expect(["pending_approval", "approved", "accepted", "filled", "rejected", "expired"].map(statusLabel)).toEqual([
      "대기",
      "승인",
      "주문",
      "체결",
      "거절",
      "만료",
    ]);
    expect(statusLabel("record_only")).toBe("record_only");
    expect(statusLabel(null)).toBe("—");
  });
});
