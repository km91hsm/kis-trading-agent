// 「오늘」 화면 순수 함수 — 레인 카드 집계·결정 목록 행·결정 상세 링크·주문 타임라인·다음 할 일·알림 종류.
// 상태값 정본: 주문 `kernel/execution/migrations.py`·`types.py`(OPEN_STATUSES), 파생 다리 `features/fo/types.py`(LEG_STATUSES),
// 결정 상태 `features/spot/decisions_view.py _order_state`(pending_approval·blocked), 승인 `approvals` CHECK.

import type { Tone } from "../../components/Badge";
import { fmtMoney, fmtPt, fmtTime, fmtWhen, untilText } from "../../lib/format";
import { GLOSSARY } from "../../lib/glossary";
import { tickerLabel } from "../../lib/ticker";

export type Lane = "spot" | "scalp" | "fo";
export const LANES: Lane[] = ["spot", "scalp", "fo"];
export const LANE_LABEL: Record<Lane, string> = { spot: "현물", scalp: "단타", fo: "파생" };

export type Account = { id: string; env: string; lane: string; currency?: string; paused?: boolean; pending_approvals?: number | null };
export type Decision = {
  asset: string;
  id: number | string;
  lane: string;
  at: string;
  account_id?: string | null;
  market?: string | null;
  ticker?: string | null;
  name?: string | null;
  side?: string | null;
  action?: string | null;
  /** 최종 의견 요약(30_decision rationale). 단타·파생은 없다. */
  summary?: string | null;
  intent?: string | null;
  direction?: string | null;
  vehicle?: string | null;
  qty?: number | null;
  status?: string | null;
};
export type Fill = { qty: number; price: string | number | null; filled_at: string };
export type Leg = { filled_qty?: number | null; price?: string | number | null; sent_at?: string | null; created_at?: string | null };
export type Order = {
  lane: string;
  status: string;
  filled_qty?: number | null;
  asset?: string;
  id?: number | string;
  at?: string;
  market?: string | null;
  ticker?: string | null;
  name?: string | null;
  side?: string | null;
  qty?: number | null;
  price?: string | number | null;
  order_price?: string | number | null;
  fills?: Fill[] | null;
  legs?: Leg[] | null;
};
export type Cap = { kind: string; used: number; cap: number | null };
export type Merged<T> = { items: T[]; partial: boolean };

export type LaneCard = {
  lane: Lane;
  decisions: { buy: number; sell: number; hold: number };
  orders: number;
  fills: number;
  open: number;
  state: "done" | "waiting" | "paused";
  capText: string | null;
};

/** 레인 → 하루 세션 상한(`GET /system/usage caps_by_kind`)의 kind. 단타는 LLM 세션이 없다. */
const CAP_KIND: Record<Lane, string | null> = { spot: "analyze", scalp: null, fo: "fo" };
/** 체결·취소를 기다리는 주문(OPEN_STATUSES) + 파생 다리의 보내기 전·중. */
const OPEN = new Set(["accepted", "unknown", "partial", "prepared", "sending"]);
/** 파생 결정 intent → 매수(진입)·매도(축소). 그 밖(HOLD 등)은 보류. */
const FO_ENTRY = new Set(["OPEN", "RESIZE_UP"]);
const FO_REDUCE = new Set(["RESIZE_DOWN", "CLOSE", "STOP", "EXPIRY", "SESSION_CLOSE", "RISK_REDUCE"]);

function sideOf(d: Decision): "buy" | "sell" | "hold" {
  const side = (d.side ?? "").toUpperCase();
  if (side === "BUY") return "buy";
  if (side === "SELL") return "sell";
  if (d.intent && FO_ENTRY.has(d.intent)) return "buy";
  if (d.intent && FO_REDUCE.has(d.intent)) return "sell";
  return "hold";
}

/** 계좌가 있는 레인마다 카드 1장(레인 순서 고정). */
export function laneCards(accounts: Account[], decisions: Decision[], orders: Order[], caps: Cap[]): LaneCard[] {
  return LANES.filter((lane) => accounts.some((a) => a.lane === lane)).map((lane) => {
    const accts = accounts.filter((a) => a.lane === lane);
    const counts = { buy: 0, sell: 0, hold: 0 };
    for (const d of decisions) if (d.lane === lane) counts[sideOf(d)] += 1;
    const mine = orders.filter((o) => o.lane === lane);
    const fills = mine.filter((o) => o.status === "filled" || (o.filled_qty ?? 0) > 0).length;
    const open = mine.filter((o) => OPEN.has(o.status)).length;
    const pending = accts.reduce((n, a) => n + (a.pending_approvals ?? 0), 0);
    const state = accts.some((a) => a.paused) ? "paused" : open > 0 || pending > 0 ? "waiting" : "done";
    const kind = CAP_KIND[lane];
    const cap = kind ? caps.find((c) => c.kind === kind) : undefined;
    const capText = cap && cap.cap !== null ? `오늘 ${cap.kind} ${cap.used}/${cap.cap}` : null;
    return { lane, decisions: counts, orders: mine.length, fills, open, state, capText };
  });
}

const STATUS_LABEL: Record<string, string> = {
  pending_approval: "대기",
  pending: "대기",
  approved: "승인",
  accepted: "주문",
  unknown: "주문",
  partial: "주문",
  prepared: "주문",
  sending: "주문",
  ordered: "주문",
  sent: "주문",
  filled: "체결",
  rejected: "거절",
  cancelled: "취소",
  failed: "실패",
  blocked: "거절",
  expired: "만료",
};

/** 결정 상태 원문 → 한국어 라벨. 모르는 값은 원문, 없으면 「—」. */
export function statusLabel(status: string | null | undefined): string {
  if (!status) return "—";
  return STATUS_LABEL[status] ?? status;
}

/** 결정 상세 경로. 단타 행(`scalp-<n>`)은 결정 상세가 없어 null. */
export function decisionHref(d: Pick<Decision, "asset" | "id" | "lane">): string | null {
  if (d.lane === "scalp" || String(d.id).startsWith("scalp-")) return null;
  return `/decisions/${d.asset}/${d.id}`;
}

export type DecisionRow = {
  key: string;
  lane: string;
  at: string;
  /** 종목 칸: 「코드 이름」(파생은 의도·방향·수단) — HOLD/BUY 같은 판단은 붙이지 않는다(방향 열이 따로 있다). */
  ticker: string;
  /** 내용 칸: 최종 의견 요약. 없으면 null(—). */
  summary: string | null;
  side: string;
  qty: number | null;
  account_id: string | null;
  status: string;
  raw: string | null;
  href: string | null;
};

const SIDE_LABEL = { buy: "매수", sell: "매도", hold: "보류" } as const;

function tickerOf(d: Decision): string {
  if (d.asset === "fo") return [d.intent, d.direction, d.vehicle].filter(Boolean).join(" ") || "—";
  return tickerLabel(d.ticker, d.name);
}

/** 결정 목록 행 — 시각 역순. */
export function decisionRows(decisions: Decision[]): DecisionRow[] {
  return [...decisions]
    .sort((a, b) => (Date.parse(b.at) || 0) - (Date.parse(a.at) || 0))
    .map((d) => ({
      key: `${d.asset}-${d.id}`,
      lane: d.lane,
      at: d.at,
      ticker: tickerOf(d),
      summary: d.summary?.trim() || null,
      side: SIDE_LABEL[sideOf(d)],
      qty: d.qty ?? null,
      account_id: d.account_id ?? null,
      status: statusLabel(d.status),
      raw: d.status ?? null,
      href: decisionHref(d),
    }));
}

// ── 주문·체결 타임라인 ──────────────────────────────────────────────────────────
export type TimelineLine = { at: string; time: string; text: string; tone: Tone };

const FAILED = new Set(["cancelled", "failed", "rejected"]);
const ORDER_SIDE: Record<string, string> = { BUY: "매수", SELL: "매도" };

/** 주문 접수 한 줄 + 체결마다 한 줄(현물·단타는 `fills`, 파생은 체결된 다리 `sent_at ?? created_at`), 시각 오름차순. */
export function timeline(orders: Order[]): TimelineLine[] {
  const out: TimelineLine[] = [];
  for (const o of orders) {
    const fo = o.asset === "fo" || o.lane === "fo";
    const unit = fo ? "계약" : "주";
    const px = (v: string | number | null | undefined) => (fo ? fmtPt(v) : fmtMoney(v, o.market === "US" ? "USD" : "KRW"));
    const at = (v: string | null | undefined) => (v ? { at: v, time: fmtTime(v) } : null);
    const price = o.price ?? o.order_price;
    const head = [ORDER_SIDE[(o.side ?? "").toUpperCase()] ?? o.side, o.ticker ? tickerLabel(o.ticker, o.name) : null, o.qty != null ? `${o.qty}${unit}` : null]
      .filter(Boolean)
      .join(" ");
    const failed = FAILED.has(o.status);
    const placed = at(o.at);
    if (placed)
      out.push({
        ...placed,
        text: `${head}${price != null ? ` @${px(price)}` : ""} · ${failed ? statusLabel(o.status) : "접수"}`,
        tone: failed ? "fail" : "muted",
      });
    for (const f of o.fills ?? []) {
      const t = at(f.filled_at);
      if (t) out.push({ ...t, text: `체결 ${f.qty}${unit}${f.price != null ? ` @${px(f.price)}` : ""}`, tone: "ok" });
    }
    for (const l of o.legs ?? []) {
      const t = at(l.sent_at ?? l.created_at);
      if (t && (l.filled_qty ?? 0) > 0)
        out.push({ ...t, text: `체결 ${l.filled_qty}${unit}${l.price != null ? ` @${px(l.price)}` : ""}`, tone: "ok" });
    }
  }
  return out.sort((a, b) => (Date.parse(a.at) || 0) - (Date.parse(b.at) || 0));
}

// ── 오늘 요약·레인 다음 예정 ────────────────────────────────────────────────────
export type TodaySummary = { decisions: number; orders: number; fills: number; failed: number; notices: number };
const FAILED_ORDER = new Set(["failed", "rejected", "cancelled"]);

/** 화면 머리 KPI: 결정·주문·체결(filled 또는 체결 수량 > 0)·실패(실패·거절·취소)·알림 건수. */
export function todaySummary(decisions: Decision[], orders: Order[], notices: Pick<Notice, "id">[]): TodaySummary {
  return {
    decisions: decisions.length,
    orders: orders.length,
    fills: orders.filter((o) => o.status === "filled" || (o.filled_qty ?? 0) > 0).length,
    failed: orders.filter((o) => FAILED_ORDER.has(o.status)).length,
    notices: notices.length,
  };
}

/** 레인 카드 「다음 예정」에 보이는 사용자 잡 — 분 단위 감시(monitor·fo_monitor)·내부 틱은 뺀다. */
const LANE_NEXT_KINDS: Record<Lane, string[]> = {
  spot: ["scan_analyze", "rebalance", "daily_report", "reflect", "briefing"],
  scalp: ["scalp"],
  fo: ["fo_session", "fo_reconcile"],
};

/** 레인의 사용자 잡 중 가장 이른 next_at(없으면 null). */
export function laneNextAt(lane: Lane, jobs: Job[]): string | null {
  const kinds = LANE_NEXT_KINDS[lane];
  const ts = jobs
    .filter((j): j is Job & { next_at: string } => kinds.includes(j.kind) && !!j.next_at)
    .map((j) => j.next_at)
    .sort((a, b) => (Date.parse(a) || 0) - (Date.parse(b) || 0));
  return ts[0] ?? null;
}

// ── 다음 할 일(데몬 작업) ───────────────────────────────────────────────────────
export type Job = { id: number | string; kind: string; next_at: string | null; last_status?: string | null };
export type Daemon = { alive: boolean; jobs?: Job[] | null };
export type NextJob = { id: number | string; kind: string; label: string; market: string | null; next_at: string; when: string; rel: string; last: string | null };

/** 「다음 할 일」에 보이는 사용자 잡 — 내부 틱(큐·텔레그램·사용량…)·분 단위 감시(monitor·scalp·theme…)는 시스템 데몬 표에서만. */
export const USER_JOB_KINDS = new Set([
  "briefing", "scan_analyze", "rebalance", "daily_report", "reconcile", "reflect", "paper_review",
  "fo_session", "fo_reconcile", "regime", "ml_train", "rl_train",
]);  // prettier-ignore
const NEXT_JOBS_MAX = 5;

/** 잡 id 접두어(kr_·us_)로 시장. */
function marketOfJob(id: number | string): string | null {
  const m = /^(kr|us)_/.exec(String(id));
  return m ? m[1].toUpperCase() : null;
}

/** 사용자 잡 중 next_at 이 있는 것을 시각 오름차순으로 5개 — 한글 라벨(glossary job.*)·시장·「N시간 뒤」·마지막 상태 원문. */
export function nextJobs(daemon: Daemon, now: Date = new Date()): NextJob[] {
  return (daemon.jobs ?? [])
    .filter((j): j is Job & { next_at: string } => USER_JOB_KINDS.has(j.kind) && !!j.next_at)
    .sort((a, b) => (Date.parse(a.next_at) || 0) - (Date.parse(b.next_at) || 0))
    .slice(0, NEXT_JOBS_MAX)
    .map((j) => ({
      id: j.id,
      kind: j.kind,
      label: GLOSSARY[`job.${j.kind}`]?.label ?? j.kind,
      market: marketOfJob(j.id),
      next_at: j.next_at,
      when: fmtWhen(j.next_at, now),
      rel: untilText(j.next_at, now),
      last: j.last_status ?? null,
    }));
}

// ── 알림 ───────────────────────────────────────────────────────────────────────
export type Notice = { id: number; at: string; kind: string; account_id: string | null; text: string; channel?: string | null; ok: boolean };
export type NoticeRow = { id: number; at: string; kind: string; kindLabel: string; account_id: string | null; title: string; body: string; full: string; ok: boolean };

const NOTICE_KIND_LABEL: Record<string, string> = { info: "정보", approval: "승인", fill: "체결", error: "오류", warn: "경고", order: "주문", report: "리포트" };
/** 알림 종류 원문 → 한글(모르면 원문). */
export function noticeKindLabel(kind: string): string {
  return NOTICE_KIND_LABEL[kind] ?? kind;
}

/** 필터 칩 목록 — 처음 본 순서, 중복 없이. */
export function noticeKinds(notices: Pick<Notice, "kind">[]): string[] {
  return [...new Set(notices.map((n) => n.kind))];
}

const TITLED = /^\[([^\]]+)\]\s*/;

/** 표 행: 본문이 「[제목] …」 꼴이면 제목을 종류 칸에, 본문은 첫 줄만(원문 전체는 full → title 속성). */
export function noticeRows(notices: Notice[]): NoticeRow[] {
  return notices.map((n) => {
    const m = TITLED.exec(n.text);
    const rest = m ? n.text.slice(m[0].length) : n.text;
    return {
      id: n.id,
      at: n.at,
      kind: n.kind,
      kindLabel: noticeKindLabel(n.kind),
      account_id: n.account_id,
      title: m ? m[1] : noticeKindLabel(n.kind),
      body: rest.split("\n")[0].trim(),
      full: n.text,
      ok: n.ok,
    };
  });
}
