// 계좌별 화면 순수 함수 — 레인 나누기(현물·단타·파생), 실시간 시세 입히기(+stale), 평가 합계(단타 제외),
// 손절·목표 칩 문구, NAV 시계열(현물 {time,value}·파생 {date,nav}), 모드 선택지(실전은 auto 를 맨 뒤 — LIVE 동작).
// 응답 정본: `kernel/web/merge.py`(items·partial), `features/spot/positions_view.py`, `features/fo/routes.py fo_rows`.
import { fmtKrw, fmtMoney, fmtPt } from "../../lib/format";
import { applyLive, isStale, type LiveFields, type QuoteRow } from "../../lib/live";

type Amount = string | number | null;
export type Level = { level: Amount; source: string } | null;
export type Lane = "spot" | "scalp" | "fo";
export type PositionRow = {
  lane: Lane | string;
  ticker: string;
  name: string | null;
  market: string;
  currency: string;
  qty: number | string | null;
  avg_price: Amount;
  last_price: Amount;
  /** 서버 잔고 스냅샷 현재가(현물 행). 시세가 없거나 낡으면 이 값으로 평가한다. */
  current_price?: Amount;
  stop: Level;
  target: Level;
  krw_value?: Amount;
  kind?: string | null;
  direction?: string | null;
  contract_side?: string | null;
  status?: string | null;
  horizon_until?: string | null;
  last_trade_date?: string | null;
};
export type LiveRow<R> = R & LiveFields & { stale: boolean };
type QuoteLike = { price: string; at: string; stale: boolean; source: string };

export function splitLanes<R extends { lane: string }>(rows: R[]): { spot: R[]; scalp: R[]; fo: R[] } {
  return {
    spot: rows.filter((r) => r.lane !== "scalp" && r.lane !== "fo"),
    scalp: rows.filter((r) => r.lane === "scalp"),
    fo: rows.filter((r) => r.lane === "fo"),
  };
}

/** `applyLive` + `stale`: 새 시세면 live. 시세가 없거나 낡았으면(isStale) live false 로 두고 서버 값(last_price → 스냅샷
 *  current_price)으로 평가한다 — 둘 다 없을 때만 낡은 시세 가격을 보인다(stale 칩과 함께). */
export function withLive<R extends PositionRow>(rows: R[], quotes: { quotes: Record<string, QuoteLike> } | null | undefined, now: number): LiveRow<R>[] {
  const all = quotes?.quotes ?? {};
  const fresh: Record<string, QuoteRow> = {};
  for (const [t, q] of Object.entries(all)) if (!isStale(q, now)) fresh[t] = q as QuoteRow;
  return rows.map((r) => {
    const q = all[r.ticker];
    const stale = !!q && isStale(q, now);
    const fallback = r.last_price ?? r.current_price ?? (stale ? q.price : null);
    const [applied] = applyLive([{ ...r, last_price: fallback }], fresh);
    return { ...applied, last_price: r.last_price, stale };
  });
}

const n = (v: Amount | undefined): number | null => {
  if (v === null || v === undefined || v === "") return null;
  const x = Number(v);
  return Number.isFinite(x) ? x : null;
};

/** 평가 합계·손익 — 같은 통화 행만 넘긴다(첫 행 통화로 형식). 현재가가 없으면 평단으로, 단타 행은 뺀다. */
export function sumEval(rows: PositionRow[]): { eval: string; pnl: string } {
  const held = rows.filter((r) => r.lane !== "scalp");
  const currency = held[0]?.currency === "USD" ? "USD" : "KRW";
  let total = 0;
  let pnl = 0;
  for (const r of held) {
    const qty = n(r.qty) ?? 0;
    const avg = n(r.avg_price);
    const price = n(r.current_price) ?? avg ?? 0;
    total += price * qty;
    if (avg !== null) pnl += (price - avg) * qty;
  }
  const p = fmtMoney(pnl, currency);
  return { eval: fmtMoney(total, currency), pnl: pnl > 0 ? `+${p}` : p };
}

/** 「<수준> · <출처>」(출처 문구는 서버 그대로: 결정·추적·기본 −7%·단타 규칙·손절 −N%). 파생은 unit "PT". */
export function stopLabel(stop: Level, unit: "KRW" | "USD" | "PT" = "KRW"): string {
  if (!stop) return "—";
  const level = unit === "PT" ? fmtPt(stop.level) : unit === "USD" ? fmtMoney(stop.level, "USD") : fmtKrw(stop.level);
  return `${level} · ${stop.source}`;
}

export type NavPerf = { nav: { time: string; value: Amount }[] };
export type NavFoReport = { nav: { date: string; nav: Amount }[] };

export function navSeries(src: NavPerf | NavFoReport | null | undefined): { time: string; value: number }[] {
  const out: { time: string; value: number }[] = [];
  for (const p of (src?.nav ?? []) as Record<string, unknown>[]) {
    const time = (p.time ?? p.date) as string | undefined;
    const value = n((p.value ?? p.nav) as Amount);
    if (time && value !== null) out.push({ time, value });
  }
  return out;
}

export type Mode = "auto" | "approve" | "off";
/** 실전은 auto 를 맨 뒤에 — 실전 auto 가 LIVE 동작이다. */
export function modeOptions(env: string): Mode[] {
  return env === "paper" ? ["auto", "approve", "off"] : ["approve", "off", "auto"];
}
