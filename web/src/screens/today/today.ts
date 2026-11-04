// 「오늘」 화면 순수 함수 — 레인 카드 집계·결정 목록 행·결정 상세 링크.
// 상태값 정본: 주문 `kernel/execution/migrations.py`·`types.py`(OPEN_STATUSES), 파생 다리 `features/fo/types.py`(LEG_STATUSES),
// 결정 상태 `features/spot/decisions_view.py _order_state`(pending_approval·blocked), 승인 `approvals` CHECK.

export type Lane = "spot" | "scalp" | "fo";
export const LANES: Lane[] = ["spot", "scalp", "fo"];
export const LANE_LABEL: Record<Lane, string> = { spot: "현물", scalp: "단타", fo: "파생" };

export type Account = { id: string; env: string; lane: string; paused?: boolean; pending_approvals?: number | null };
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
export type Order = { lane: string; status: string; filled_qty?: number | null };
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
