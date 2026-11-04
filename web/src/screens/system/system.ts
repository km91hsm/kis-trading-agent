// 「시스템」 화면 순수 함수 — 응답 타입과 표·한 줄 변환. 레벨 기준(70/90)은 셸과 같은 `usageLevel` 을 쓴다.
import type { Tone } from "../../components/Badge";
import { now as clockNow } from "../../lib/clock";
import { fmtDate, fmtTime } from "../../lib/format";
import { resetsIn, usageLevel, type LimitWindow, type Throttle, type UsageLevel, type UsageResp } from "../../lib/usage";

const DASH = "—";

// ── 사용량 ───────────────────────────────────────────────────────────────────
export type UsageMeter = {
  key: "5h" | "7d" | "spend";
  label: string;
  used_pct: number | null;
  level: UsageLevel;
  observed: string | null;
  resets: string | null;
  source: string | null;
};

const WINDOWS = [
  { key: "5h", label: "5시간", pick: "five_hour" },
  { key: "7d", label: "7일", pick: "seven_day" },
  { key: "spend", label: "지출 한도", pick: "spend_limit" },
] as const;

/** 5시간·7일·지출 한도 미터. 관측 없는 창은 used_pct null(레벨 ok — 화면은 「—」). `now` 는 리셋 남은 시간용. */
export function usageMeters(usage: UsageResp, now: Date = clockNow()): UsageMeter[] {
  const rl = usage.rate_limits;
  return WINDOWS.map(({ key, label, pick }) => {
    const w: LimitWindow = rl ? rl[pick] : null;
    const pct = w?.used_pct ?? null;
    const age = w ? (w.age_s ?? rl?.age_s ?? null) : null;
    const left = resetsIn(w?.resets_at, now);
    return {
      key,
      label,
      used_pct: pct,
      level: pct === null ? "ok" : usageLevel(pct),
      observed: age === null ? null : `${Math.floor(age / 60)}분 전`,
      resets: left ? `리셋까지 ${left}` : null,
      source: rl?.source ?? null,
    };
  });
}

const THROTTLE_STATE: Record<Throttle["state"], string> = { ok: "정상", soft: "완화", hard: "중단" };

/** 「사용량 조절 켜짐(설정 파일) · 정상(완화 70% · 중단 90%) — 사유」. */
export function throttleLine(t: Throttle): string {
  const src = t.source === "control" ? "화면 설정" : "설정 파일";
  const head = `사용량 조절 ${t.enabled ? "켜짐" : "꺼짐"}(${src}) · ${THROTTLE_STATE[t.state] ?? t.state}(완화 ${t.soft_pct}% · 중단 ${t.hard_pct}%)`;
  return t.reason ? `${head} — ${t.reason}` : head;
}

// ── 잡 ───────────────────────────────────────────────────────────────────────
export type JobKind = { kind: string; label: string; lane: string; trading: boolean; params_schema: unknown };
export type ScheduledJob = { id: string; kind: string; trigger: string; schedule_key: string | null; interval_s: number | null; next_at: string | null };
export type JobsResp = { kinds: JobKind[]; jobs: ScheduledJob[] };

export type KindRow = { kind: string; label: string; lane: string; trading: boolean };
export type ScheduleRow = { id: string; kind: string; label: string; trigger: string; next_at: string | null };

export function jobRows(jobs: JobsResp): { kinds: KindRow[]; schedules: ScheduleRow[] } {
  const names = new Map(jobs.kinds.map((k) => [k.kind, k.label]));
  return {
    kinds: jobs.kinds.map((k) => ({ kind: k.kind, label: k.label || k.kind, lane: k.lane, trading: k.trading })),
    schedules: jobs.jobs.map((j) => ({ id: j.id, kind: j.kind, label: names.get(j.kind) || j.kind, trigger: j.trigger, next_at: j.next_at })),
  };
}

export type JobRequest = {
  id: number;
  kind: string;
  params: Record<string, unknown> | null;
  status: string;
  source: string | null;
  requested_at: string | null;
  started_at: string | null;
  finished_at: string | null;
  progress: unknown;
  error: string | null;
  result: unknown;
  priority: number | null;
  label: string | null;
};
export type RequestsResp = { alive: boolean; active: number; requests: JobRequest[] };

const STATUS: Record<string, { text: string; tone: Tone }> = {
  queued: { text: "대기", tone: "muted" },
  running: { text: "실행 중", tone: "warn" },
  ok: { text: "완료", tone: "ok" },
  error: { text: "오류", tone: "fail" },
  skipped: { text: "건너뜀", tone: "muted" },
};
const statusOf = (s: string) => STATUS[s] ?? { text: s, tone: "muted" as Tone };

export type RequestRow = {
  id: number;
  kind: string;
  label: string;
  status: string;
  tone: Tone;
  cancellable: boolean;
  requested_at: string | null;
  finished_at: string | null;
  error: string | null;
  params: string;
};

export function requestRows(requests: JobRequest[]): RequestRow[] {
  return requests.map((r) => {
    const st = statusOf(r.status);
    return {
      id: r.id,
      kind: r.kind,
      label: r.label || r.kind,
      status: st.text,
      tone: st.tone,
      cancellable: r.status === "queued",
      requested_at: r.requested_at,
      finished_at: r.finished_at,
      error: r.error,
      params: r.params && Object.keys(r.params).length ? JSON.stringify(r.params) : "",
    };
  });
}

// ── runs·calls ───────────────────────────────────────────────────────────────
export type Run = {
  id: number;
  kind: string;
  market: string | null;
  started_at: string | null;
  finished_at: string | null;
  status: string;
  error: string | null;
  result_json: unknown;
};
export type RunRow = { id: number; kind: string; market: string; status: string; tone: Tone; started_at: string | null; duration: string; error: string | null };

function seconds(from: string | null, to: string | null): string {
  if (!from || !to) return DASH;
  const s = (new Date(to).getTime() - new Date(from).getTime()) / 1000;
  return Number.isFinite(s) ? String(Math.round(s)) : DASH;
}

export function runRows(runs: Run[]): RunRow[] {
  return runs.map((r) => {
    const st = statusOf(r.status);
    return {
      id: r.id,
      kind: r.kind,
      market: r.market ?? DASH,
      status: r.status,
      tone: st.tone,
      started_at: r.started_at,
      duration: seconds(r.started_at, r.finished_at),
      error: r.error,
    };
  });
}

export type Call = {
  id: number;
  run_id: number | null;
  ticker: string | null;
  market: string | null;
  agent: string | null;
  model: string | null;
  cost_usd: string | null;
  duration_ms: number | null;
  status: string;
  error: string | null;
  num_turns: number | null;
  created_at: string | null;
};
export type CallsResp = { calls: Call[]; sessions_today: number; cost_today: string | number | null };
export type CallRow = { id: number; ticker: string; agent: string; model: string; status: string; tone: Tone; cost: string; duration: string; turns: string; created_at: string | null };

export function callRows(calls: Call[]): CallRow[] {
  return calls.map((c) => {
    const cost = c.cost_usd === null || c.cost_usd === "" ? NaN : Number(c.cost_usd);
    return {
      id: c.id,
      ticker: c.ticker ?? DASH,
      agent: c.agent ?? DASH,
      model: c.model ?? DASH,
      status: c.status,
      tone: statusOf(c.status).tone,
      cost: Number.isFinite(cost) ? cost.toFixed(4) : DASH,
      duration: c.duration_ms === null ? DASH : (c.duration_ms / 1000).toFixed(1),
      turns: c.num_turns === null ? DASH : String(c.num_turns),
      created_at: c.created_at,
    };
  });
}

// ── 조작 이력 ────────────────────────────────────────────────────────────────
export type ControlEntry = { id: number; at: string; channel: string; actor: string | null; key: string; old: unknown; new: unknown };

function val(v: unknown): string {
  if (v === null || v === undefined) return DASH;
  return typeof v === "string" ? v : JSON.stringify(v);
}

/** 「2026-11-05 10:00 · web · ohs · account_mode:real-1 · approve → auto」(KST). */
export function controlRows(log: ControlEntry[]): { id: number; line: string }[] {
  return log.map((e) => ({
    id: e.id,
    line: `${fmtDate(e.at)} ${fmtTime(e.at).slice(0, 5)} · ${e.channel} · ${e.actor ?? DASH} · ${e.key} · ${val(e.old)} → ${val(e.new)}`,
  }));
}

// ── 시크릿 ───────────────────────────────────────────────────────────────────
export type SecretKey = { name: string; group: string; label: string; account: string | null; set: boolean; env: boolean };
export type SecretsResp = { keys: SecretKey[]; accounts: { id: string; env: string; uses: string[]; prefix: string }[] };
export type SecretRow = { name: string; label: string; group: string; account: string | null; set: boolean; env: boolean };

export function secretRows(secrets: SecretsResp): SecretRow[] {
  return secrets.keys.map((k) => ({ name: k.name, label: k.label, group: k.group, account: k.account, set: k.set, env: k.env }));
}

// ── 기능 상태 응답(화면에 쓰는 칸만) ─────────────────────────────────────────
export type RegimeMarket = { market: string; state: string; count: number; since: string | null; asof: string | null };
export type RegimeResp = { enabled: boolean; max_weight_pct: number; markets: RegimeMarket[]; killswitch: { on: boolean; reason: string | null } };
export type SavetickerResp = {
  configured: boolean;
  backend: string;
  service: { state: string; reason: string | null; at: string | null } | null;
  blocked: boolean;
  reason_text: string | null;
  last_success_at: string | null;
  health: string | null;
};
export type ScalpResp = { enabled: boolean; judge: boolean; max_weight_pct: number; max_positions: number; markets: Record<string, { open: unknown[]; closed: unknown[]; halted: boolean }> };
export type FoAccount = { id: string; env: string; mode: string; paused: boolean; state: string | null; enabled: boolean; triggers: unknown[] };
export type FoResp = { accounts: FoAccount[]; context: { regime?: { state?: string } | null; market_view?: { stance?: string } | null } | null };
