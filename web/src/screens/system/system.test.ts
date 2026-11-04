import { describe, expect, test } from "vitest";
import { fixtureUsage } from "../../test/fixtures";
import { fixtureCalls, fixtureControlLog, fixtureJobs, fixtureRequests, fixtureRuns, fixtureSecrets } from "../../test/fixtures/system";
import {
  callRows,
  controlRows,
  jobRows,
  requestRows,
  runRows,
  secretRows,
  throttleLine,
  usageMeters,
  type CallsResp,
  type ControlEntry,
  type JobsResp,
  type RequestsResp,
  type Run,
  type SecretsResp,
} from "./system.ts";

const NOW = new Date("2026-11-05T10:00:00+09:00");

describe("시스템 순수 함수", () => {
  test("usageMeters: 5시간·7일·지출 3개, 레벨(70/90)·관측 「N분 전」·리셋 글귀·출처", () => {
    const u = fixtureUsage({ five_h_pct: 72, seven_d_pct: 95, five_h_resets_at: "2026-11-05T11:20:00+09:00" });
    u.rate_limits!.age_s = 300;
    const m = usageMeters(u, NOW);
    expect(m.map((x) => x.key)).toEqual(["5h", "7d", "spend"]);
    expect(m[0]).toEqual({ key: "5h", label: "5시간", used_pct: 72, level: "warn", observed: "5분 전", resets: "리셋까지 1시간 20분", source: "statusline" });
    expect(m[1]).toMatchObject({ label: "7일", used_pct: 95, level: "fail", resets: null });
    expect(m[2]).toMatchObject({ label: "지출 한도", used_pct: null, level: "ok", observed: null, resets: null });
    expect(usageMeters(fixtureUsage({ five_h_pct: 10 }), NOW)[0].level).toBe("ok");
  });

  test("usageMeters: 관측이 없으면(rate_limits null) 모두 null", () => {
    const u = fixtureUsage();
    u.rate_limits = null;
    const m = usageMeters(u, NOW);
    expect(m).toHaveLength(3);
    expect(m.every((x) => x.used_pct === null && x.observed === null && x.source === null)).toBe(true);
  });

  test("throttleLine: 켜짐·출처·상태·사유 한 문장", () => {
    const t = fixtureUsage().throttle;
    expect(throttleLine(t)).toBe("사용량 조절 켜짐(설정 파일) · 정상(완화 70% · 중단 90%)");
    expect(throttleLine({ ...t, enabled: false, source: "control", state: "hard", reason: "5시간 92%" })).toBe(
      "사용량 조절 꺼짐(화면 설정) · 중단(완화 70% · 중단 90%) — 5시간 92%",
    );
  });

  test("jobRows: kind 표 + 일정(다음 실행·이름)", () => {
    const r = jobRows(fixtureJobs() as JobsResp);
    expect(r.kinds.map((k) => [k.kind, k.label, k.trading])).toEqual([
      ["spot_trade", "현물 매매", true],
      ["features_snapshot", "지표 스냅샷", false],
    ]);
    expect(r.schedules).toEqual([{ id: "spot.trade.KR", kind: "spot_trade", label: "현물 매매", trigger: "cron", next_at: "2026-11-05T10:30:00+09:00" }]);
  });

  test("requestRows: 상태 칩·취소 가능 = queued 만·params 한 줄", () => {
    const rows = requestRows((fixtureRequests() as RequestsResp).requests);
    expect(rows.map((r) => [r.id, r.status, r.tone, r.cancellable])).toEqual([
      [7, "실행 중", "warn", false],
      [8, "대기", "muted", true],
      [5, "오류", "fail", false],
    ]);
    expect(rows[0].params).toBe('{"market":"KR"}');
    expect(rows[1].params).toBe("");
    expect(rows[2].error).toBe("타임아웃");
  });

  test("runRows·callRows: 상태 칩, 비용 USD 4자리·소요 초·턴", () => {
    const runs = runRows(fixtureRuns() as Run[]);
    expect(runs[0]).toMatchObject({ id: 31, kind: "spot_trade", market: "KR", status: "ok", tone: "ok", duration: "90" });
    expect(runs[1]).toMatchObject({ tone: "fail", duration: "—", error: "브로커 오류" });
    const calls = callRows((fixtureCalls() as CallsResp).calls);
    expect(calls[0]).toMatchObject({ id: 101, cost: "0.0123", duration: "12.3", turns: "3", ticker: "005930" });
    expect(calls[1]).toMatchObject({ cost: "—", duration: "—", turns: "—", ticker: "—" });
  });

  test("controlRows: 「시각 · 채널 · 누가 · 키 · 전 → 후」 한 줄(문자열 그대로·그 밖 JSON·null 은 —)", () => {
    const rows = controlRows(fixtureControlLog() as ControlEntry[]);
    expect(rows[0].line).toBe("2026-11-05 10:00 · web · ohs · account_mode:real-1 · approve → auto");
    expect(rows[1].line).toBe('2026-11-05 09:00 · telegram · — · paused · — → {"on":true}');
  });

  test("secretRows: 이름·라벨·설정 여부·환경변수 우선(값은 없음)", () => {
    const rows = secretRows(fixtureSecrets() as SecretsResp);
    expect(rows[0]).toEqual({ name: "KIS_PAPER_APP_KEY", label: "모의 앱 키", group: "kis", account: "paper-1", set: true, env: false });
    expect(rows.map((r) => [r.set, r.env])).toEqual([
      [true, false],
      [false, false],
      [true, true],
    ]);
  });
});
