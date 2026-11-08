// 옛 `web/src/trace.ts` 이식
// 결정 추적 — `/decisions/{asset}/{id}/trace` 응답과 행 변환(순수 함수).
// 새 백엔드 모양(spot/decisions_view.trace·fo/routes.decision_trace)은 normalizeTrace 로 옛 TraceResp 틀에 맞춘다.
import { GLOSSARY, sideLabel, termLabel } from "./glossary";

export type TraceAnalyst = { role: string; label: string; signal: string | null; confidence: number | null; summary: string | null; evidence: string[] };
/** timing 은 세션에 따라 문자열("NOW")이거나 객체({action, condition, price_zone}) — 정규화해 action 은 timing, 조건은 timing_note. */
export type DebateIssue = { issue_id: string; topic: string; claim: string; evidence: string[] };
export type DebateResponse = { issue_id: string; response: string; evidence: string[] };
export type TraceDebate = { round: number | null; side: string | null; stance: string | null; conviction: number | null; timing: string | null; timing_note: string | null; lane?: string | null; provider?: string | null; model?: string | null; issues?: DebateIssue[]; responses?: DebateResponse[]; replies?: DebateResponse[] };
export type TraceGate = { stage: string; account_id: string; approved?: boolean; qty?: number | null; reasons?: string[]; note?: string | null; mode?: string | null; dry_run?: boolean | null };
export type TraceOrder = { stage: string; account_id: string; status?: string | null; order_no?: string | null; message?: string | null; side?: string | null; qty?: number | null; price?: string | null; mode?: string | null };
/** fo 게이트·주문 체크포인트 요약(`fo/routes._checks` 값). */
export type FoCheck = { approved?: boolean | null; reasons?: string[]; note?: string | null; status?: string | null; record_only?: boolean | number | null; error?: string | null };
export type TraceResp = {
  decision: { id: number; date: string; market: string; ticker: string; action: string; data?: Record<string, unknown> };
  quote: string | null;
  analysts: TraceAnalyst[];
  debate: TraceDebate[];
  research: { rounds?: number; gap_round0?: number; gap_final?: number; sycophantic?: boolean | null; preferred?: string | null; skipped_debate?: boolean; reason?: string } | null;
  gate: TraceGate[];
  orders: TraceOrder[];
  reevaluations: { round: string; decision: Record<string, unknown>; gate: TraceGate[]; orders: TraceOrder[] }[];
  stop_loss: { gate: TraceGate[]; orders: TraceOrder[] };
  reprice: { gate: TraceGate[]; orders: TraceOrder[] };
  stages: string[];
  /** fo 만: 게이트·주문 체크포인트 요약, 결정 체크포인트 값. */
  checks?: Record<string, FoCheck>;
  checkpoint?: Record<string, unknown> | null;
};

export const SIGNAL = { BULLISH: "강세", NEUTRAL: "중립", BEARISH: "약세", UNKNOWN: "판단불가", BUY: "매수", SELL: "매도", HOLD: "보류" } as Record<string, string>;

export type TraceStep = { key: string; title: string; lines: string[] };

/** 판 이름: 없으면 1판("a"). */
export const laneLabel = (lane: string | null | undefined) => (lane === "b" ? "2판" : "1판");

const pct = (v: number | null | undefined) => (v === null || v === undefined ? "—" : v.toFixed(2));

/** 추적 응답 → 단계별 줄 목록(애널리스트 → 토론 → 수렴 → 결정 → 게이트 → 주문 → 재평가·손절·재호가). */
export function traceSteps(t: TraceResp): TraceStep[] {
  const steps: TraceStep[] = [];
  steps.push({
    key: "analysts",
    title: `애널리스트 ${t.analysts.length}`,
    lines: t.analysts.map((a) => `${a.label}: ${SIGNAL[a.signal ?? ""] ?? a.signal ?? "—"} · 확신 ${pct(a.confidence)}${a.evidence.length ? ` · ${a.evidence.join(" / ")}` : ""}`),
  });
  const rounds = [...new Set(t.debate.map((d) => d.round ?? 0))].sort((a, b) => a - b);
  const hasLaneB = t.debate.some((d) => d.lane === "b");
  steps.push({
    key: "debate",
    title: `토론 ${rounds.length}라운드`,
    lines: rounds.map((r) => {
      const turns = t.debate.filter((d) => (d.round ?? 0) === r);
      const named = hasLaneB ? [...turns].sort((x, y) => (x.lane === "b" ? 1 : 0) - (y.lane === "b" ? 1 : 0) || (x.side === "bull" ? 0 : 1) - (y.side === "bull" ? 0 : 1)) : turns;
      return `r${r}: ${named.map((d) => `${hasLaneB ? `${laneLabel(d.lane)} ` : ""}${d.side === "bull" ? "강세" : "약세"} ${SIGNAL[d.stance ?? ""] ?? d.stance ?? "—"} ${pct(d.conviction)}`).join(" · ")}`;
    }),
  });
  if (t.research) {
    steps.push({
      key: "research",
      title: "수렴 감시",
      lines: [t.research.skipped_debate ? `토론 생략 · ${t.research.reason ?? "애널리스트 합의"}` : t.research.sycophantic ? `붕괴(아첨적 수렴) · 라운드 0 우선 · 차이 ${pct(t.research.gap_round0)}→${pct(t.research.gap_final)}` : `정상 · 차이 ${pct(t.research.gap_round0)}→${pct(t.research.gap_final)}`],
    });
  }
  const d = t.decision.data ?? {};
  steps.push({
    key: "decision",
    title: `결정 ${t.decision.action}`,
    lines: [`비중 ${pct(d.target_weight as number | undefined)} · 확신 ${pct(d.confidence as number | undefined)} · 보유 ${String(d.horizon_days ?? "—")}일${t.quote ? ` · 당시 현재가 ${t.quote}` : ""}`],
  });
  steps.push({ key: "gate", title: `게이트 ${t.gate.length}계좌`, lines: t.gate.map(gateLine) });
  steps.push({ key: "orders", title: `주문 ${t.orders.length}`, lines: t.orders.map(orderLine) });
  for (const r of t.reevaluations) {
    steps.push({ key: `re-${r.round}`, title: `재평가 ${r.round}: ${String(r.decision.action ?? "—")}`, lines: [...r.gate.map(gateLine), ...r.orders.map(orderLine)] });
  }
  if (t.stop_loss.gate.length || t.stop_loss.orders.length) {
    steps.push({ key: "stop", title: "손절", lines: [...t.stop_loss.gate.map(gateLine), ...t.stop_loss.orders.map(orderLine)] });
  }
  if (t.reprice.gate.length || t.reprice.orders.length) {
    steps.push({ key: "reprice", title: "재호가", lines: [...t.reprice.gate.map(gateLine), ...t.reprice.orders.map(orderLine)] });
  }
  return steps;
}

export function gateLine(g: TraceGate): string {
  const head = g.approved ? `${termLabel("gate.pass")} ${g.qty ?? 0}주` : termLabel("gate.reject");
  const why = (g.reasons ?? []).join("; ");
  return `${g.account_id}: ${head}${why ? ` · ${why}` : ""}${g.note ? ` (${g.note})` : ""}${g.dry_run ? ` · ${termLabel("dry_run")}` : ""}`;
}

export function orderLine(o: TraceOrder): string {
  return `${o.account_id}: ${sideLabel(o.side)} ${o.qty ?? ""}주${o.price ? ` @${o.price}` : " 시장가"} → ${o.status ? (GLOSSARY[`status.${o.status}`]?.label ?? o.status) : "—"}${o.order_no ? ` #${o.order_no}` : ""}${o.message ? ` · ${o.message}` : ""}`;
}

/** fo 체크포인트 요약 한 줄: `<단계>: 통과|거부|<상태> · 사유 (메모) · 오류`. */
export function checkLine(stage: string, c: FoCheck): string {
  const head = c.approved === true ? termLabel("gate.pass") : c.approved === false ? termLabel("gate.reject") : c.status ? (GLOSSARY[`status.${c.status}`]?.label ?? c.status) : "—";
  const why = (c.reasons ?? []).join("; ");
  return `${stage}: ${head}${why ? ` · ${why}` : ""}${c.note ? ` (${c.note})` : ""}${c.error ? ` · ${c.error}` : ""}`;
}

/** 지정가까지 거리(%): 당시 현재가 대비 지정가. 시장가·현재가 없음은 null. */
export function limitDistancePct(price: string | null | undefined, quote: string | null | undefined): number | null {
  const p = price ? Number(price) : NaN;
  const q = quote ? Number(quote) : NaN;
  if (!Number.isFinite(p) || !Number.isFinite(q) || q <= 0) return null;
  return ((p - q) / q) * 100;
}

/** unknown 주문 경고줄 문구. 없으면 "". */
export function unknownLine(orders: { status: string }[]): string {
  const n = orders.filter((o) => o.status === "unknown").length;
  return n ? `확인 필요 ${n}건 — 응답을 받지 못한 주문(unknown). 체결 동기화가 확정하지 못하면 KIS 앱에서 확인하세요.` : "";
}

// ── 새 모양 어댑터 ────────────────────────────────────────────────────────────
type Obj = Record<string, unknown>;
const isObj = (v: unknown): v is Obj => typeof v === "object" && v !== null && !Array.isArray(v);
const arr = (v: unknown): Obj[] => (Array.isArray(v) ? v.filter(isObj) : []);
const strOrNull = (v: unknown): string | null => (v === null || v === undefined || v === "" ? null : String(v));

/** 배열이면 빠진 stage 만 채우고, 계좌별 dict(`{account_id: {...}}`)면 배열로 편다. */
function withStage<T>(v: unknown, stage: string): T[] {
  if (Array.isArray(v)) return arr(v).map((x) => ({ ...x, stage: x.stage ?? stage }) as T);
  if (isObj(v)) return Object.entries(v).filter(([, x]) => isObj(x)).map(([account_id, x]) => ({ stage, account_id, ...(x as Obj) }) as T);
  return [];
}
function pair(v: unknown, stage: string): { gate: TraceGate[]; orders: TraceOrder[] } {
  const o = isObj(v) ? v : {};
  return { gate: withStage<TraceGate>(o.gate, stage), orders: withStage<TraceOrder>(o.orders, stage) };
}

/** 새 `/trace` 응답 → TraceResp. spot 은 stage·배열 기본값을 채우고, fo 축소형은 빈 토론·게이트·주문으로 접는다. */
export function normalizeTrace(asset: "spot" | "fo", raw: unknown): TraceResp {
  const r = isObj(raw) ? raw : {};
  const dec = isObj(r.decision) ? r.decision : {};
  const stages = Array.isArray(r.stages) ? r.stages.map(String) : [];
  if (asset === "fo") {
    return {
      decision: { id: Number(dec.id), date: String(dec.date ?? ""), market: "", ticker: String(dec.vehicle ?? ""), action: String(dec.intent ?? ""), data: dec },
      quote: null,
      analysts: arr(r.analysts).map((a) => ({
        role: String(a.role ?? ""),
        label: String(a.label ?? a.role ?? ""),
        signal: strOrNull(a.signal),
        confidence: typeof a.confidence === "number" ? a.confidence : null,
        summary: strOrNull(a.summary),
        evidence: Array.isArray(a.evidence) ? a.evidence.filter((x: unknown) => x != null).map(String) : [],
      })),
      debate: [],
      research: null,
      gate: [],
      orders: [],
      reevaluations: [],
      stop_loss: { gate: [], orders: [] },
      reprice: { gate: [], orders: [] },
      stages,
      checks: isObj(r.checks) ? (r.checks as Record<string, FoCheck>) : {},
      checkpoint: isObj(r.checkpoint) ? r.checkpoint : null,
    };
  }
  const data = isObj(dec.data) ? dec.data : {};
  return {
    decision: {
      id: Number(dec.id),
      date: String(dec.date ?? ""),
      market: String(dec.market ?? ""),
      ticker: String(dec.ticker ?? ""),
      action: String(dec.action ?? ""),
      data: { target_weight: dec.target_weight, confidence: dec.confidence, horizon_days: dec.horizon_days, ...data },
    },
    quote: strOrNull(r.quote),
    analysts: arr(r.analysts).map((a) => ({ ...a, evidence: Array.isArray(a.evidence) ? a.evidence.filter((x: unknown) => x != null).map(String) : [] }) as TraceAnalyst),
    debate: arr(r.debate).map((d) => {
      const t = d.timing;
      const obj = isObj(t) ? t : null;
      return {
        round: typeof d.round === "number" ? d.round : null,
        side: strOrNull(d.side),
        lane: strOrNull(d.lane),
        provider: strOrNull(d.provider),
        model: strOrNull(d.model),
        issues: arr(d.issues).map((x) => ({ issue_id: String(x.issue_id ?? ""), topic: String(x.topic ?? ""), claim: String(x.claim ?? ""), evidence: Array.isArray(x.evidence) ? x.evidence.map(String) : [] })),
        responses: arr(d.responses).map((x) => ({ issue_id: String(x.issue_id ?? ""), response: String(x.response ?? ""), evidence: Array.isArray(x.evidence) ? x.evidence.map(String) : [] })),
        replies: arr(d.replies).map((x) => ({ issue_id: String(x.issue_id ?? ""), response: String(x.response ?? ""), evidence: Array.isArray(x.evidence) ? x.evidence.map(String) : [] })),
        stance: strOrNull(d.stance),
        conviction: typeof d.conviction === "number" ? d.conviction : null,
        timing: obj ? strOrNull(obj.action) : strOrNull(t),
        timing_note: obj ? [strOrNull(obj.condition), strOrNull(obj.price_zone)].filter(Boolean).join(" · ") || null : null,
      };
    }),
    research: isObj(r.research) ? (r.research as TraceResp["research"]) : null,
    gate: withStage<TraceGate>(r.gate, "risk"),
    orders: withStage<TraceOrder>(r.orders, "order"),
    reevaluations: arr(r.reevaluations).map((x) => ({
      round: typeof x.round === "number" ? `r${x.round}` : String(x.round ?? ""),
      decision: isObj(x.decision) ? x.decision : { action: x.action, stop_loss: x.stop_loss, source: x.source },
      gate: withStage<TraceGate>(x.gate, "reeval"),
      orders: withStage<TraceOrder>(x.orders, "reeval"),
    })),
    stop_loss: pair(r.stop_loss, "stop_loss"),
    reprice: pair(r.reprice, "reprice"),
    stages,
  };
}
