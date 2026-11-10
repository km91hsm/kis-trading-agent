// 「계좌·보유」 통합 화면 순수 함수 — 원화·달러 합계와 환율 줄, 계좌 카드 행, 전역 재개의 실전 확인 대상.
// 응답 정본: `features/accounts/overview.py`(account_rows·totals), `routes.py set_paused`(해제는 실전 auto 계좌 id 하나를 confirm).
import { fmtDate, fmtMoney, fmtUsd } from "../../lib/format";

type Amount = string | number | null;
export type MarketTotal = { nav: Amount; day_pnl: Amount; accounts: string[] };
export type Fx = { pair: string; rate: Amount; as_of: string | null; converted_total: Amount };
export type OverviewGroup = { krw: MarketTotal; usd: MarketTotal; fx: Fx | null; paused: boolean; at: string };
export type Overview = OverviewGroup & { by_env?: Partial<Record<"paper" | "real", OverviewGroup>> };
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
  /** 결제 예정 매수대금을 뺀 주문 가능 금액. 옛 기록·파생 계좌는 없다. */
  cash_available?: Amount;
  cash_at?: string | null;
  usd_nav?: Amount;
  day_pnl: Amount;
  positions_count: number | null;
  pending_approvals: number | null;
};

export type Totals = {
  krw: string;
  krwPnl: string;
  krwScope: string;
  usd: string;
  usdPnl: string;
  usdScope: string;
  fxLine: string | null;
  converted: string | null;
};

/** `GET /fo/summary` 의 계좌 한 묶음(fo/routes.py _account_status) — 화면이 쓰는 키만. */
export type FoStatus = {
  id: string;
  state: string;
  nav_date?: string | null;
  nav: Amount;
  cash?: Amount;
  margin_total?: Amount;
  margin_pct?: number | null;
};
export type FoSummary = { accounts: FoStatus[] };

export const FO_STATE_LABEL: Record<string, string> = {
  off: "실전 꺼짐",
  disabled: "레인 꺼짐",
  unverified: "검증 전",
  halted: "손실 한도",
  record_only: "기록만",
  entries_paused: "새 진입 멈춤",
  active: "활성",
};

/** 금액에 통화 단위 — 원화 「10,000,000원」, 달러 「$2,500.50」. null 은 「—」. */
export function moneyUnit(v: Amount | undefined, currency: string): string {
  const usd = currency === "USD";
  const s = fmtMoney(v ?? null, usd ? "USD" : "KRW");
  if (s === "—") return s;
  if (!usd) return `${s}원`;
  return s.startsWith("-") ? `-$${s.slice(1)}` : `$${s}`; // 부호는 기호 앞: -$12.25
}

/** 합계에 든 계좌 id 를 모의/실전으로 세어 「모의 1 · 실전 1」. 계좌 목록 전이면 「계좌 n」. */
export function scopeText(ids: string[], accounts: Account[] | null | undefined): string {
  if (!accounts) return `계좌 ${ids.length}`;
  const env = new Map(accounts.map((a) => [a.id, a.env]));
  let paper = 0;
  let live = 0;
  for (const id of ids) (env.get(id) === "paper" ? paper++ : live++);
  const parts = [];
  if (paper) parts.push(`모의 ${paper}`);
  if (live) parts.push(`실전 ${live}`);
  return parts.length ? parts.join(" · ") : "계좌 없음";
}
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

export function totals(o: OverviewGroup, accounts?: Account[] | null): Totals {
  const krw = (v: Amount) => moneyUnit(v, "KRW");
  const usd = (v: Amount) => moneyUnit(v, "USD");
  return {
    krw: krw(o.krw.nav),
    krwPnl: signed(o.krw.day_pnl, krw),
    krwScope: scopeText(o.krw.accounts, accounts),
    usd: usd(o.usd.nav),
    usdPnl: signed(o.usd.day_pnl, usd),
    usdScope: scopeText(o.usd.accounts, accounts),
    fxLine: o.fx ? `적용 환율 ${o.fx.pair} ${fmtUsd(o.fx.rate)} (${asOfText(o.fx.as_of)})` : "환율 없음",
    converted: o.fx ? `환산 합계 ${moneyUnit(o.fx.converted_total, "KRW")}` : null,
  };
}

export function cardRows(accounts: Account[]): AccountCard[] {
  return accounts.map((a) => {
    const money = (v: Amount) => moneyUnit(v, a.currency);
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
