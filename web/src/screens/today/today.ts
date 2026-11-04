// 「오늘」 화면 순수 함수 — 레인 카드 집계·결정 목록 행·결정 상세 링크·주문 타임라인·다음 할 일·알림 종류.
// 상태값 정본: 주문 `kernel/execution/migrations.py`·`types.py`(OPEN_STATUSES), 파생 다리 `features/fo/types.py`(LEG_STATUSES),
// 결정 상태 `features/spot/decisions_view.py _order_state`(pending_approval·blocked), 승인 `approvals` CHECK.

import type { Tone } from "../../components/Badge";
import { fmtMoney, fmtPt, fmtTime } from "../../lib/format";

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
  side?: string | null;
  action?: string | null;
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
  title: string;
  side: string;
  qty: number | null;
  account_id: string | null;
  status: string;
  raw: string | null;
  href: string | null;
};

const SIDE_LABEL = { buy: "매수", sell: "매도", hold: "보류" } as const;

function titleOf(d: Decision): string {
  if (d.asset === "fo") return [d.intent, d.direction, d.vehicle].filter(Boolean).join(" ");
  return [d.ticker, d.action && d.action !== d.side ? d.action : null].filter(Boolean).join(" ");
}

/** 결정 목록 행 — 시각 역순. */
export function decisionRows(decisions: Decision[]): DecisionRow[] {
  return [...decisions]
    .sort((a, b) => (Date.parse(b.at) || 0) - (Date.parse(a.at) || 0))
    .map((d) => ({
      key: `${d.asset}-${d.id}`,
      lane: d.lane,
      at: d.at,
      title: titleOf(d) || "—",
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
    const head = [ORDER_SIDE[(o.side ?? "").toUpperCase()] ?? o.side, o.ticker, o.qty != null ? `${o.qty}${unit}` : null]
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

// ── 다음 할 일(데몬 작업) ───────────────────────────────────────────────────────
export type Job = { id: number | string; kind: string; next_at: string | null; last_status?: string | null };
export type Daemon = { alive: boolean; jobs?: Job[] | null };
export type NextJob = { id: number | string; kind: string; next_at: string; text: string };

/** 다음 실행 시각이 있는 작업을 시각 오름차순으로 3개. 마지막 실행이 실패였으면 꼬리표. */
export function nextJobs(daemon: Daemon): NextJob[] {
  return (daemon.jobs ?? [])
    .filter((j): j is Job & { next_at: string } => !!j.next_at)
    .sort((a, b) => (Date.parse(a.next_at) || 0) - (Date.parse(b.next_at) || 0))
    .slice(0, 3)
    .map((j) => ({
      id: j.id,
      kind: j.kind,
      next_at: j.next_at,
      text: `${j.kind} · ${fmtTime(j.next_at)}${j.last_status === "error" || j.last_status === "failed" ? " (마지막 실패)" : ""}`,
    }));
}

// ── 알림 ───────────────────────────────────────────────────────────────────────
export type Notice = { id: number; at: string; kind: string; account_id: string | null; text: string; channel?: string | null; ok: boolean };

/** 필터 칩 목록 — 처음 본 순서, 중복 없이. */
export function noticeKinds(notices: Pick<Notice, "kind">[]): string[] {
  return [...new Set(notices.map((n) => n.kind))];
}
