// 「결정 상세」 화면 순수 함수 — 머리(제목 코드+이름·칩·근거 줄)·라운드 행·토론 타이밍 라벨.
// 응답 모양 정본: spot `features/spot/decisions_view.detail`, fo `features/fo/routes.py`(`GET /decisions/fo/{id}`).
import { fmtTime } from "../../lib/format";
import { tickerLabel } from "../../lib/ticker";

export type SpotDecision = {
  id: number;
  date: string;
  market: string;
  ticker: string;
  name?: string | null;
  action: string;
  confidence?: number | null;
  rationale?: string | null;
  source?: string | null;
  status?: string | null;
  asset?: undefined;
};
export type FoDecision = {
  id: number;
  date: string;
  asset: "fo";
  intent?: string | null;
  direction?: string | null;
  vehicle?: string | null;
  confidence?: number | null;
  rationale?: string | null;
  status?: string | null;
  account_id?: string | null;
};
export type Round = { round: number; action: string; stop_loss?: string | number | null; source?: string | null; created_at?: string | null };
export type SpotDetail = { decision: SpotDecision; rounds: Round[]; summary?: string | null; news_refs?: unknown[] };
export type FoDetail = { decision: FoDecision; data?: unknown; legs?: unknown[] };

export const ACTION_LABEL: Record<string, string> = { BUY: "매수", ADD: "추가", SELL: "매도", TRIM: "축소", HOLD: "보류" };
export const actionLabel = (a: string | null | undefined) => (a ? (ACTION_LABEL[a] ?? a) : "—");

const lines = (s: string | null | undefined) => (s ?? "").split("\n").map((x) => x.trim()).filter(Boolean);

/** 결정 → 제목 `종목 행동`(fo 는 intent·direction·vehicle)·칩(날짜·시장·확신·출처·상태)·근거 줄. */
export function decisionHeader(d: SpotDecision | FoDecision): { title: string; chips: string[]; summaryLines: string[] } {
  const fo = d.asset === "fo";
  const body = fo
    ? [d.intent, d.direction, d.vehicle].filter(Boolean).join(" ")
    : `${tickerLabel((d as SpotDecision).ticker, (d as SpotDecision).name ?? null)} ${actionLabel((d as SpotDecision).action)}`;
  const conf = typeof d.confidence === "number" ? `확신 ${Math.round(d.confidence * 100)}%` : null;
  const chips = [d.date, fo ? null : (d as SpotDecision).market, conf, fo ? null : (d as SpotDecision).source, d.status];
  return { title: body, chips: chips.filter((c): c is string => !!c), summaryLines: lines(d.rationale) };
}

const TIMING_LABEL: Record<string, string> = { NOW: "지금", WAIT: "대기", NEVER: "안 함" };
export const timingLabel = (t: string | null | undefined) => (t ? (TIMING_LABEL[t] ?? t) : "—");

export type RoundRow = { round: number; action: string; stop_loss: string; source: string; at: string };

/** 재평가 라운드 → 표시 행(행동 한글·시각 KST). */
export function roundRows(rounds: Round[]): RoundRow[] {
  return rounds.map((r) => ({
    round: r.round,
    action: r.action,
    stop_loss: r.stop_loss === null || r.stop_loss === undefined ? "—" : String(r.stop_loss),
    source: r.source ?? "—",
    at: fmtTime(r.created_at),
  }));
}
