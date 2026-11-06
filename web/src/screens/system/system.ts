// 「시스템」 화면 순수 함수 — 응답 타입과 표·한 줄 변환. 레벨 기준(70/90)은 셸과 같은 `usageLevel` 을 쓴다.
import type { Tone } from "../../components/Badge";
import { GLOSSARY } from "../../lib/glossary";
import { now as clockNow } from "../../lib/clock";
import { fmtPct, fmtTime } from "../../lib/format";
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

/** 「사용량 조절 켜짐(설정 파일) · 정상(완화 80% · 중단 95%) — 사유」. soft/hard 는 비율(0.8). */
export function throttleLine(t: Throttle): string {
  const src = t.source === "control" ? "화면 설정" : "설정 파일";
  const head = `사용량 조절 ${t.enabled ? "켜짐" : "꺼짐"}(${src}) · ${THROTTLE_STATE[t.state] ?? t.state}(완화 ${fmtPct(t.soft_pct, 0)} · 중단 ${fmtPct(t.hard_pct, 0)})`;
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
/** 잡·실행 상태 → 한글 칩(데몬·잡 큐·실행 기록이 같은 표를 쓴다). 모르는 값은 그대로. */
export const statusOf = (s: string | null | undefined): { text: string; tone: Tone } =>
  s ? (STATUS[s] ?? { text: s, tone: "muted" as Tone }) : { text: DASH, tone: "muted" as Tone };

/** 잡 요청 인자 한 줄: null 은 빼고 market 은 값만, years 는 「N년」, 그 밖은 키=값. 없으면 "". */
export function summarizeArgs(params: Record<string, unknown> | null | undefined): string {
  if (!params) return "";
  const head: string[] = [];
  const rest: string[] = [];
  for (const [k, v] of Object.entries(params)) {
    if (v === null || v === undefined || v === "" || v === false || (Array.isArray(v) && v.length === 0)) continue; // 잡음(trigger_ids=[] · dry_run=false)
    if (k === "market") head.unshift(String(v));
    else if (k === "years") head.push(`${v}년`);
    else rest.push(`${k}=${typeof v === "string" ? v : JSON.stringify(v)}`);
  }
  return [...head, ...rest].join(" · ");
}

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
      params: summarizeArgs(r.params),
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

/** 소요 「<1초」·「4초」·「1분 36초」·「2시간 3분」. 시작·끝이 없거나 깨지면 —. */
export function durationText(from: string | null, to: string | null): string {
  if (!from || !to) return DASH;
  const s = (new Date(to).getTime() - new Date(from).getTime()) / 1000;
  if (!Number.isFinite(s) || s < 0) return DASH;
  if (s < 1) return "<1초";
  const whole = Math.round(s);
  if (whole < 60) return `${whole}초`;
  const m = Math.floor(whole / 60);
  if (m < 60) return whole % 60 ? `${m}분 ${whole % 60}초` : `${m}분`;
  const h = Math.floor(m / 60);
  return m % 60 ? `${h}시간 ${m % 60}분` : `${h}시간`;
}

export function runRows(runs: Run[]): RunRow[] {
  return runs.map((r) => {
    const st = statusOf(r.status);
    return {
      id: r.id,
      kind: r.kind,
      market: r.market ?? DASH,
      status: st.text,
      tone: st.tone,
      started_at: r.started_at,
      duration: durationText(r.started_at, r.finished_at),
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

export type ControlRow = { id: number; at: string; channel: string; actor: string; key: string; old: string; new: string };

/** 조작 이력 표 행 — 값은 문자열 그대로, 그 밖은 JSON, null 은 —. */
export function controlRows(log: ControlEntry[]): ControlRow[] {
  return log.map((e) => ({ id: e.id, at: e.at, channel: e.channel, actor: e.actor ?? DASH, key: e.key, old: val(e.old), new: val(e.new) }));
}

// ── 데몬 잡 표 ───────────────────────────────────────────────────────────────
export type DaemonJob = {
  id: string;
  kind: string;
  trigger: string;
  schedule_key: string | null;
  interval_s: number | null;
  next_at: string | null;
  last_start: string | null;
  last_end: string | null;
  last_status: string | null;
  last_error: string | null;
};
export type DaemonRow = {
  id: string;
  label: string;
  market: string;
  status: string;
  tone: Tone;
  schedule: string;
  next_at: string | null;
  last_end: string | null;
  duration: string;
  error: string | null;
};

/** 데몬이 제 살림으로 도는 틱 잡 — 사용자 일정이 아니라 접어 둔다. */
export const INTERNAL_KINDS = new Set([
  "job_queue",
  "job_queue_fast",
  "job_queue_reeval",
  "job_queue_info",
  "telegram_poll",
  "usage_probe",
  "approvals_expire",
  "balance_refresh",
  "ops_check",
  "retention",
  "ledger_backup",
  "names_fill",
  "saveticker",
]);

const MARKET_PREFIX: Record<string, string> = { kr_: "KR", us_: "US" };

/** 주기 글귀: 60 의 배수면 「N분마다」(3600 의 배수면 「N시간마다」), 아니면 「N초마다」. */
export function intervalText(s: number): string {
  if (s % 3600 === 0) return `${s / 3600}시간마다`;
  if (s % 60 === 0) return `${s / 60}분마다`;
  return `${s}초마다`;
}

/** 잡 표 행: 라벨은 /jobs 라벨 → glossary job.<kind> → kind, 시장은 id 접두(kr_·us_),
 *  일정은 주기면 intervalText, 예약(cron)이면 다음 실행의 KST 시각 「HH:MM」(내부 schedule_key 는 노출하지 않음), 소요는 마지막 실행. */
export function daemonRows(jobs: DaemonJob[], labels: Map<string, string>): { user: DaemonRow[]; internal: DaemonRow[] } {
  const rows = jobs.map((j): DaemonRow => {
    const st = statusOf(j.last_status);
    const prefix = Object.keys(MARKET_PREFIX).find((p) => j.id.startsWith(p));
    const schedule = j.trigger === "interval" && j.interval_s ? intervalText(j.interval_s) : j.next_at ? fmtTime(j.next_at).slice(0, 5) : "—";
    return {
      id: j.id,
      label: labels.get(j.kind) || GLOSSARY[`job.${j.kind}`]?.label || j.kind,
      market: prefix ? MARKET_PREFIX[prefix] : "",
      status: st.text,
      tone: st.tone,
      schedule,
      next_at: j.next_at,
      last_end: j.last_end,
      duration: durationText(j.last_start, j.last_end),
      error: j.last_error,
    };
  });
  return { user: rows.filter((r) => !INTERNAL_KINDS.has(jobs.find((j) => j.id === r.id)!.kind)), internal: rows.filter((r) => INTERNAL_KINDS.has(jobs.find((j) => j.id === r.id)!.kind)) };
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
