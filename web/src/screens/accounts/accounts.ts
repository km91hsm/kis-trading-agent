// 「계좌·보유」 통합 화면 순수 함수 — 원화·달러 합계와 환율 줄, 계좌 카드 행, 전역 재개의 실전 확인 대상.
// 응답 정본: `features/accounts/overview.py`(account_rows·totals), `routes.py set_paused`(해제는 실전 auto 계좌 id 하나를 confirm).
import { fmtDate, fmtKrw, fmtMoney, fmtUsd } from "../../lib/format";

type Amount = string | number | null;
export type MarketTotal = { nav: Amount; day_pnl: Amount; accounts: string[] };
export type Fx = { pair: string; rate: Amount; as_of: string | null; converted_total: Amount };
export type Overview = { krw: MarketTotal; usd: MarketTotal; fx: Fx | null; paused: boolean; at: string };
export type Account = {
  id: string;
  env: string;
  lane: string;
  markets?: string[];
  currency: string;
  mode: string;
  mode_note?: string | null;
  paused: boolean;
  nav: Amount;
  cash?: Amount;
  cash_at?: string | null;
  usd_nav?: Amount;
  day_pnl: Amount;
  positions_count: number | null;
  pending_approvals: number | null;
};

export type Totals = { krw: string; krwPnl: string; usd: string; usdPnl: string; fxLine: string | null; converted: string | null };
export type AccountCard = {
  id: string;
  href: string;
  lane: string;
  envLabel: string;
  live: boolean;
  mode: string;
  modeNote: string | null;
  paused: boolean;
  nav: string;
  dayPnl: string;
  positions: number;
  pending: number;
};

const LANE_LABEL: Record<string, string> = { spot: "현물", scalp: "단타", fo: "파생" };
const MODE_LABEL: Record<string, string> = { auto: "자동", approve: "승인", off: "끔" };

/** 양수면 「+」를 붙인다(음수는 형식 함수가 「-」를 붙임, null 은 「—」). */
function signed(v: Amount, fmt: (v: Amount) => string): string {
  const s = fmt(v);
  return v !== null && Number(v) > 0 ? `+${s}` : s;
}

/** 날짜로 읽히면 `fmtDate`, 아니면(「11월 4일 종가」 같은 문구) 그대로. */
function asOfText(asOf: string | null): string {
  if (!asOf) return "—";
  return Number.isNaN(new Date(asOf).getTime()) && !/^\d{4}-\d{2}-\d{2}$/.test(asOf) ? asOf : fmtDate(asOf);
}

export function totals(o: Overview): Totals {
  return {
    krw: fmtKrw(o.krw.nav),
    krwPnl: signed(o.krw.day_pnl, fmtKrw),
    usd: fmtUsd(o.usd.nav),
    usdPnl: signed(o.usd.day_pnl, fmtUsd),
    fxLine: o.fx ? `적용 환율 ${o.fx.pair} ${fmtUsd(o.fx.rate)} (${asOfText(o.fx.as_of)})` : "환율 없음",
    converted: o.fx ? `환산 합계 ${fmtKrw(o.fx.converted_total)}` : null,
  };
}

export function cardRows(accounts: Account[]): AccountCard[] {
  return accounts.map((a) => {
    const money = (v: Amount) => fmtMoney(v, a.currency === "USD" ? "USD" : "KRW");
    return {
      id: a.id,
      href: `/accounts/${encodeURIComponent(a.id)}`,
      lane: LANE_LABEL[a.lane] ?? a.lane,
      envLabel: a.env === "paper" ? "모의" : "실전",
      live: a.env !== "paper",
      mode: MODE_LABEL[a.mode] ?? a.mode,
      modeNote: a.mode_note ?? null,
      paused: !!a.paused,
      nav: money(a.nav),
      dayPnl: signed(a.day_pnl, money),
      positions: a.positions_count ?? 0,
      pending: a.pending_approvals ?? 0,
    };
  });
}

/** 전역 재개에 재입력할 실전 auto 계좌 id(첫 번째). 없으면 null — 확인 없이 해제. */
export function resumeNeedsConfirm(accounts: Account[]): string | null {
  return accounts.find((a) => a.env !== "paper" && a.mode === "auto")?.id ?? null;
}
