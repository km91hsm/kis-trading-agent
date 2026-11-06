import { describe, expect, test } from "vitest";
import { decisionHref, decisionRows, foLaneInfo, laneCards, laneNextAt, nextJobs, noticeKinds, noticeRows, statusLabel, timeline, todaySummary } from "./today.ts";
import { pipelineOf, riskCheckRows, stageLabel } from "./morning.ts";
import { fixtureFoToday, fixtureMarketToday, fixtureRiskCheck } from "../../test/fixtures/today";
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

  test("laneNextAt: 레인의 사용자 잡(현물 스캔·리밸런스·리포트, 단타, 파생 세션) 중 가장 이른 next_at — 분 단위 감시 잡은 제외", () => {
    const jobs = [
      fixtureJob({ id: "kr_monitor", kind: "monitor", next_at: "2026-11-05T01:01:00+00:00" }),
      fixtureJob({ id: "kr_scan_analyze", kind: "scan_analyze", next_at: "2026-11-05T22:30:00+00:00" }),
      fixtureJob({ id: "kr_rebalance", kind: "rebalance", next_at: "2026-11-05T05:30:00+00:00" }),
      fixtureJob({ id: "kr_scalp", kind: "scalp", next_at: "2026-11-05T01:00:50+00:00" }),
      fixtureJob({ id: "kr_fo_session", kind: "fo_session", next_at: null }),
    ];
    expect(laneNextAt("spot", jobs)).toBe("2026-11-05T05:30:00+00:00");
    expect(laneNextAt("scalp", jobs)).toBe("2026-11-05T01:00:50+00:00");
    expect(laneNextAt("fo", jobs)).toBeNull();
    expect(laneNextAt("spot", [])).toBeNull();
  });

  test("todaySummary: 결정·주문·체결·실패·알림 건수", () => {
    const s = todaySummary(
      [fixtureSpotDecision(), fixtureScalpDecision(), fixtureFoDecision()],
      [fixtureSpotOrder({ status: "filled" }), fixtureSpotOrder({ id: 2, status: "failed", filled_qty: 0 }), fixtureSpotOrder({ id: 3, status: "rejected", filled_qty: 0 }), fixtureSpotOrder({ id: 4, status: "accepted", filled_qty: 0 })],
      [fixtureNotice(), fixtureNotice({ id: 2 })],
    );
    expect(s).toEqual({ decisions: 3, orders: 4, fills: 1, failed: 2, notices: 2 });
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

  test("nextJobs: 사용자 잡만(내부 틱·분 단위 감시 제외) next_at 오름차순 5개 — 한글 라벨·시장·상대 시각·마지막 상태", () => {
    const now = new Date("2026-11-05T01:00:00Z");
    const daemon = fixtureDaemon([
      fixtureJob({ id: "kr_scan_analyze", kind: "scan_analyze", next_at: "2026-11-05T03:00:00+00:00" }),
      fixtureJob({ id: "kr_fo_session", kind: "fo_session", next_at: null }),
      fixtureJob({ id: "us_rebalance", kind: "rebalance", next_at: "2026-11-05T01:30:00+00:00", last_status: "error" }),
      fixtureJob({ id: "job_queue", kind: "job_queue", next_at: "2026-11-05T01:00:10+00:00" }),
      fixtureJob({ id: "kr_monitor", kind: "monitor", next_at: "2026-11-05T01:01:00+00:00" }),
      fixtureJob({ id: "reflect", kind: "reflect", next_at: "2026-11-05T05:00:00+00:00" }),
      fixtureJob({ id: "morning_briefing", kind: "briefing", next_at: "2026-11-05T22:20:00+00:00" }),
      fixtureJob({ id: "paper_review", kind: "paper_review", next_at: "2026-11-06T00:00:00+00:00" }),
      fixtureJob({ id: "kr_daily_report", kind: "daily_report", next_at: "2026-11-06T07:20:00+00:00" }),
    ]);
    const rows = nextJobs(daemon, now);
    expect(rows.map((r) => r.id)).toEqual(["us_rebalance", "kr_scan_analyze", "reflect", "morning_briefing", "paper_review"]);
    expect(rows[0]).toMatchObject({ label: "리밸런스", market: "US", rel: "30분 뒤", last: "error" });
    expect(rows[1]).toMatchObject({ label: "스캔 → 분석", market: "KR", rel: "2시간 뒤" });
    expect(rows[2].market).toBeNull();
  });

  test("noticeRows: 「[제목] 본문」 꼴이면 제목·첫 줄 본문, 아니면 종류 한글 라벨 + 첫 줄; 원문은 full", () => {
    const [a, b] = noticeRows([
      fixtureNotice({ id: 9, kind: "info", text: "[점검 격상] 관심 → 주의\n24시간 안에 오류로 끝난 잡 3건" }),
      fixtureNotice({ id: 8, kind: "approval", text: "승인 요청 — 005930 매수 3주" }),
    ]);
    expect(a).toMatchObject({ id: 9, title: "점검 격상", body: "관심 → 주의", kindLabel: "정보" });
    expect(a.full).toBe("[점검 격상] 관심 → 주의\n24시간 안에 오류로 끝난 잡 3건");
    expect(b).toMatchObject({ id: 8, title: "승인", body: "승인 요청 — 005930 매수 3주", kindLabel: "승인" });
  });

  test("noticeKinds: 처음 본 순서대로 중복 없이", () => {
    const notices = [fixtureNotice({ id: 3, kind: "fill" }), fixtureNotice({ id: 2, kind: "approval" }), fixtureNotice({ id: 1, kind: "fill" })];
    expect(noticeKinds(notices)).toEqual(["fill", "approval"]);
  });
});

describe("아침 파이프라인·리스크 게이트 순수 함수", () => {
  test("stageLabel: 체크포인트 이름 → glossary stage.* 한글(접미사 무시), 모르는 이름은 그대로", () => {
    expect(stageLabel("00_inputs")).toBe("입력");
    expect(stageLabel("10_analyst_technical")).toBe("애널리스트");
    expect(stageLabel("20_debate_r0_bull")).toBe("토론");
    expect(stageLabel("30_decision")).toBe("결정");
    expect(stageLabel("40_risk_check_paper-1")).toBe("리스크 게이트");
    expect(stageLabel("56_order_hedge_paper-main")).toBe("헤지 주문");
    expect(stageLabel("99_whatever")).toBe("99_whatever");
  });

  test("pipelineOf: 시장 단계 칩(스캔·시장 세션·분석)과 종목 행(코드+이름·마지막 단계·단계 수), 발굴 줄, 빈 날은 null", () => {
    const kr = pipelineOf(fixtureMarketToday("KR"))!;
    expect(kr.steps.map((s) => [s.id, s.done])).toEqual([["scan", true], ["market", true], ["analyze", true]]);
    expect(kr.rows.map((r) => [r.ticker, r.label, r.last, r.count])).toEqual([
      ["005930", "005930 삼성전자", "리스크 게이트", 5],
      ["000660", "000660", "애널리스트", 2],
    ]);
    expect(kr.decided).toBe(1);
    expect(kr.picks).toBe("005930 삼성전자 돌파(실적) · 000660 수급");
    // 발굴 사유가 긴 문장이면 첫 절(;)까지 40자로 줄이고 원문은 picksFull 에
    const long = pipelineOf(fixtureMarketToday("KR", { scan: { picks: [{ ticker: "000660", reason: "모멘텀 상위, 52주 고점 근접; 거래대금 상위 3개 사유 모두 충족. 점수 최상위.", discovery: null }], targets: 1, cap_hit: false, discovery: null } }))!;
    expect(long.picks).toBe("000660 모멘텀 상위, 52주 고점 근접…");
    expect(long.picksFull).toContain("점수 최상위");
    expect(kr.newsJobs).toBe(1);
    expect(kr.capHit).toBe(false);
    expect(pipelineOf(fixtureMarketToday("US"))).toBeNull();
    const noScan = pipelineOf(fixtureMarketToday("KR", { scan: null, market_files: [] }))!;
    expect(noScan.steps.map((s) => s.done)).toEqual([false, false, true]);
    expect(noScan.picks).toBeNull();
  });

  test("riskCheckRows: 시각·계좌·종목(코드+이름)·결과·수량 조정·사유 한 줄·결정 링크", () => {
    const rows = riskCheckRows([fixtureRiskCheck(), fixtureRiskCheck({ id: 2, approved: true, adjusted_qty: 3, reasons: [], name: null, ticker: "000660", decision_id: 9 })]);
    expect(rows[0]).toMatchObject({ id: 1, account_id: "paper-1", ticker: "005930 삼성전자", approved: false, adjusted: "—", reasons: "주간 한도 · 현금 부족", href: "/decisions/spot/1", market: "KR" });
    // 거부면 수량 조정은 의미가 없으니 0 이어도 「—」
    expect(riskCheckRows([fixtureRiskCheck({ adjusted_qty: 0 })])[0].adjusted).toBe("—");
    expect(rows[1]).toMatchObject({ ticker: "000660", approved: true, adjusted: "3주", reasons: "—", href: "/decisions/spot/9" });
  });
});

describe("파생 당일(foLaneInfo)", () => {
  test("아침 결정 한 줄(의도·방향·수단·확신)·재평가 횟수·계좌 플래그, 결정 없으면 line null", () => {
    const i = foLaneInfo(fixtureFoToday());
    expect(i.line).toBe("아침 결정 진입 롱 · 선물 · 확신 70%");
    expect(i.reevals).toBe(1);
    expect(i.flags).toEqual([{ id: "paper-fo", halted: true, pending: true }]);
    const none = foLaneInfo(fixtureFoToday({ decision: null, reevals: [], accounts: [] }));
    expect(none).toEqual({ line: null, reevals: 0, flags: [] });
  });
});
