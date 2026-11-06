import { describe, expect, test } from "vitest";
import { decisionHref, decisionRows, laneCards, nextJobs, noticeKinds, statusLabel, timeline } from "./today.ts";
import {
  fixtureAccount,
  fixtureDaemon,
  fixtureFoDecision,
  fixtureFoOrder,
  fixtureJob,
  fixtureNotice,
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
    expect(statusLabel("cancelled")).toBe("취소");
    expect(statusLabel("failed")).toBe("실패");
    expect(statusLabel("record_only")).toBe("record_only");
    expect(statusLabel(null)).toBe("—");
  });

  test("timeline: 주문 1건 + 체결 2건 → 3줄, 시:분:초 KST", () => {
    const order = fixtureSpotOrder({
      qty: 10,
      price: "70000",
      status: "filled",
      filled_qty: 10,
      at: "2026-11-05T01:01:00+00:00",
      fills: [
        { qty: 6, price: "70100", filled_at: "2026-11-05T01:05:07+00:00" },
        { qty: 4, price: "70200", filled_at: "2026-11-05T01:03:09+00:00" },
      ],
    });
    const lines = timeline([order]);
    expect(lines.map((l) => l.time)).toEqual(["10:01:00", "10:03:09", "10:05:07"]);
    expect(lines.map((l) => l.text)).toEqual(["매수 005930 10주 @70,000 · 접수", "체결 4주 @70,200", "체결 6주 @70,100"]);
    expect(lines.map((l) => l.tone)).toEqual(["muted", "ok", "ok"]);
  });

  test("decisionRows: 종목 칸은 코드+이름만(HOLD 같은 판단은 안 붙임), 내용은 최종 의견 요약(summary)", () => {
    const [hold, noName, scalp] = decisionRows([
      fixtureSpotDecision({ id: 9, ticker: "035420", name: "NAVER", action: "HOLD", side: null, summary: "밸류에이션 부담, 관망", at: "2026-11-05T03:00:00+00:00" }),
      fixtureSpotDecision({ id: 8, ticker: "000660", name: null, summary: null, at: "2026-11-05T02:30:00+00:00" }),
      fixtureScalpDecision({ id: "scalp-12", name: "삼성전자", at: "2026-11-05T02:00:00+00:00" }),
    ]);
    expect(hold.ticker).toBe("035420 NAVER");
    expect(hold.summary).toBe("밸류에이션 부담, 관망");
    expect(hold.side).toBe("보류");
    expect(noName.ticker).toBe("000660");
    expect(noName.summary).toBeNull();
    expect(scalp.ticker).toBe("005930 삼성전자");
  });

  test("timeline: 주문 줄도 종목 코드+이름", () => {
    const [line] = timeline([fixtureSpotOrder({ name: "삼성전자", fills: [], filled_qty: 0, status: "accepted", at: "2026-11-05T01:01:00+00:00" })]);
    expect(line.text).toBe("매수 005930 삼성전자 3주 @70,000 · 접수");
  });

  test("timeline: 미국 주문은 달러, 취소는 fail, 파생 다리 체결은 sent_at 로 한 줄", () => {
    const us = fixtureSpotOrder({ id: 22, market: "US", ticker: "AAPL", side: "SELL", qty: 2, price: "190.5", status: "cancelled", filled_qty: 0, fills: [], at: "2026-11-05T00:00:00+00:00" });
    const fo = fixtureFoOrder();
    const lines = timeline([us, fo]);
    expect(lines.map((l) => [l.time, l.text, l.tone])).toEqual([
      ["09:00:00", "매도 AAPL 2주 @190.50 · 취소", "fail"],
      ["09:30:00", "매수 A0166000 2계약 @350.25 · 접수", "muted"],
      ["09:31:00", "체결 2계약 @350.30", "ok"],
    ]);
  });

  test("nextJobs: next_at 오름차순 3개, null 제외", () => {
    const daemon = fixtureDaemon([
      fixtureJob({ id: 1, kind: "analyze", next_at: "2026-11-05T03:00:00+00:00" }),
      fixtureJob({ id: 2, kind: "fo", next_at: null }),
      fixtureJob({ id: 3, kind: "scalp", next_at: "2026-11-05T01:30:00+00:00", last_status: "error" }),
      fixtureJob({ id: 4, kind: "news", next_at: "2026-11-05T02:00:00+00:00" }),
      fixtureJob({ id: 5, kind: "report", next_at: "2026-11-05T05:00:00+00:00" }),
    ]);
    expect(nextJobs(daemon)).toEqual([
      { id: 3, kind: "scalp", next_at: "2026-11-05T01:30:00+00:00", text: "scalp · 10:30:00 (마지막 실패)" },
      { id: 4, kind: "news", next_at: "2026-11-05T02:00:00+00:00", text: "news · 11:00:00" },
      { id: 1, kind: "analyze", next_at: "2026-11-05T03:00:00+00:00", text: "analyze · 12:00:00" },
    ]);
  });

  test("noticeKinds: 처음 본 순서대로 중복 없이", () => {
    const notices = [fixtureNotice({ id: 3, kind: "fill" }), fixtureNotice({ id: 2, kind: "approval" }), fixtureNotice({ id: 1, kind: "fill" })];
    expect(noticeKinds(notices)).toEqual(["fill", "approval"]);
  });
});
