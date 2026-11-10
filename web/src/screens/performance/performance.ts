// 「성과」 화면 순수 함수 — 필터 기본값(첫 계좌·첫 시장·30일), 계좌×시장 비교 행(metrics null → 「NAV 없음」),
// 보정 run×역할 행(report null → 「표본 부족」), 복기 메모리 행, 벤치마크 선, 섀도 기준선 행.
// 응답 정본: `features/evaluation/analytics_routes.py`·`analytics_view.py`·`calibration.py`, `fo/analytics_routes.py`.
import { fmtDate, fmtPct } from "../../lib/format";
import { termLabel } from "../../lib/glossary";
import { tickerLabel } from "../../lib/ticker";
import { navSeries } from "../accounts/positions.ts";

type Num = number | null;
export type Days = "30" | "90" | "365";
export type Filters = { account_id: string; market: string; days: Days };
export type FilterAccount = { id: string; lane: string; markets?: string[] };

export type Metrics = { return: Num; mdd: Num; sortino: Num; days: Num; start: string | null; end: string | null };
type Point = { time: string; value: string | number | null };
export type Benchmark = { return: Num; series: Point[] } | null;
export type Perf = {
  account_id: string;
  market: string;
  days: number;
  nav: Point[];
  metrics: Metrics;
  benchmarks: { one_over_n: Benchmark; buy_and_hold: Benchmark; quant: Benchmark; benchmark_universe: string[] };
  cost_sensitivity: Record<string, number>;
  turnover: Num;
  decision_distribution: Record<string, number>;
  convergence_rate: Num;
};
export type Compare = { days: number; rows: { account_id: string; env: string; lane: string; market: string; currency: string; metrics: Metrics | null }[] };
type RoleStat = { hit_rate: Num; n: number; neighbor_hit_rate: Num; neighbor_n?: number; bias: Num };
export type Calibration = {
  runs: { id: number; created_at: string; n_decisions: number; active: boolean; report: null | { roles: Record<string, RoleStat> } }[];
};
type ShadowStat = { n: number; mean_diff: Num; ci_low: Num; ci_high: Num };
export const SHADOW_KEYS = ["candidates_1n", "quant_topn", "index_hold", "index_trend"] as const;
export type ShadowKey = (typeof SHADOW_KEYS)[number];
export type Shadow = {
  market: string;
  min_pairs: number;
  pairs: Partial<Record<ShadowKey, ShadowStat>>;
  recent: ({ date: string; llm?: Num } & Partial<Record<ShadowKey, Num>>)[];
};
export type MemoryEntry = {
  id: number;
  ticker: string;
  name?: string | null;
  market: string;
  decided_at: string | null;
  situation_summary: string | null;
  decision: { action?: string } | null;
  tier: string | null;
  importance: Num;
  price_return: Num;
};
export type MemoryResp = { query: unknown; entries: MemoryEntry[]; warmup?: boolean; breaker?: string | null };

export function defaultFilters(accounts: FilterAccount[]): Filters {
  const a = accounts[0];
  return { account_id: a?.id ?? "", market: a?.markets?.[0] ?? "", days: "30" };
}

/** 계좌 목록이 도착한 뒤 URL 선택을 검증한다. 지원하지 않는 값은 해당 계좌의 첫 시장·30일로 보정한다. */
export function filtersFromSearch(accounts: FilterAccount[], params: URLSearchParams): Filters {
  const account = accounts.find((a) => a.id === params.get("account_id")) ?? accounts[0];
  const markets = account?.markets ?? [];
  const requested = params.get("market") ?? "";
  const days = params.get("days");
  return {
    account_id: account?.id ?? "",
    market: markets.includes(requested) ? requested : markets[0] ?? "",
    days: days === "90" || days === "365" ? days : "30",
  };
}

export function filtersSearch(params: URLSearchParams, filters: Filters): URLSearchParams {
  const next = new URLSearchParams(params);
  for (const [key, value] of Object.entries(filters)) {
    if (value) next.set(key, value);
    else next.delete(key);
  }
  return next;
}

const fixed2 = (v: Num) => (v === null || v === undefined ? "—" : v.toFixed(2));
const signed2 = (v: Num) => (v === null || v === undefined ? "—" : `${v > 0 ? "+" : ""}${v.toFixed(2)}`);

export type CompareRow = {
  key: string;
  account_id: string;
  env: string;
  lane: string;
  market: string;
  currency: string;
  ret: string;
  mdd: string;
  sortino: string;
  note: string;
};

export function compareRows(compare: Compare): CompareRow[] {
  return compare.rows.map((r) => ({
    key: `${r.account_id}-${r.market}`,
    account_id: r.account_id,
    env: r.env,
    lane: r.lane,
    market: r.market,
    currency: r.currency,
    ret: fmtPct(r.metrics?.return ?? null),
    mdd: fmtPct(r.metrics?.mdd ?? null),
    sortino: fixed2(r.metrics?.sortino ?? null),
    note: r.metrics ? "" : "기록 없음",
  }));
}

/** 비교 행이 없거나 전부 NAV 기록이 없으면 표 대신 한 줄(「N일 기록 없음」). */
export function compareAllEmpty(compare: Compare): boolean {
  return compare.rows.every((r) => !r.metrics);
}

export type CalibrationRow = {
  key: string;
  run_id: number;
  created_at: string;
  active: boolean;
  role: string;
  hit_rate: string;
  n: number | null;
  neighbor_hit_rate: string;
  bias: string;
  note: string;
};

export function calibrationBins(calib: Calibration): CalibrationRow[] {
  return calib.runs.flatMap((run): CalibrationRow[] => {
    const base = { run_id: run.id, created_at: fmtDate(run.created_at), active: run.active };
    if (!run.report) {
      return [{ ...base, key: `${run.id}-—`, role: "—", hit_rate: "—", n: null, neighbor_hit_rate: "—", bias: "—", note: "표본 부족" }];
    }
    return Object.entries(run.report.roles).map(([role, s]) => ({
      ...base,
      key: `${run.id}-${role}`,
      role,
      hit_rate: fmtPct(s.hit_rate),
      n: s.n,
      neighbor_hit_rate: fmtPct(s.neighbor_hit_rate),
      bias: signed2(s.bias),
      note: "",
    }));
  });
}

export type MemoryRow = {
  id: number;
  ticker: string;
  market: string;
  decided_at: string;
  action: string;
  outcome: string;
  tier: string;
  importance: Num;
  summary: string;
};

export function memoryRows(mem: MemoryResp): MemoryRow[] {
  return mem.entries.map((e) => ({
    id: e.id,
    ticker: tickerLabel(e.ticker, e.name),
    market: e.market,
    decided_at: fmtDate(e.decided_at),
    action: e.decision?.action ?? "—",
    outcome: fmtPct(e.price_return),
    tier: e.tier ? termLabel(`tier.${e.tier}`) : "—",
    importance: e.importance,
    summary: e.situation_summary ?? "",
  }));
}

/** 첫 값 기준 성장 지수(시작=1.0) — 벤치마크(시작=1.0)와 같은 축에 겹치기 위해. 첫 값이 0·없음이면 빈 배열. */
export function normalizeNav(series: { time: string; value: number }[]): { time: string; value: number }[] {
  const base = series[0]?.value;
  if (!base) return [];
  return series.map((p) => ({ time: p.time, value: p.value / base }));
}

const BENCH: [keyof Perf["benchmarks"], string][] = [
  ["one_over_n", "1/N 균등"],
  ["buy_and_hold", "매수 후 보유"],
  ["quant", "정량 상위"],
];

/** NAV 차트에 겹칠 벤치마크 선 — null·빈 시리즈는 건너뛴다. */
export function benchmarkLines(perf: Perf): { label: string; series: { time: string; value: number }[] }[] {
  return BENCH.flatMap(([k, label]) => {
    const b = perf.benchmarks?.[k] as Benchmark | undefined;
    const series = b ? navSeries({ nav: b.series ?? [] }) : [];
    return series.length ? [{ label, series }] : [];
  });
}

const SHADOW_LABEL: Record<ShadowKey, string> = {
  candidates_1n: "같은 후보 균등(1/N)",
  quant_topn: "정량 상위만",
  index_hold: "지수 보유",
  index_trend: "지수 추세(100일선)",
};
export const shadowLabel = (k: ShadowKey) => SHADOW_LABEL[k];

export type ShadowRow = { key: ShadowKey; label: string; n: number; mean: string; ci: string; note: string };

/** 섀도 기준선별 LLM − 기준선 평균차와 95% 구간. 만기 쌍이 min_pairs 미만이면 메모. */
export function shadowRows(shadow: Shadow): ShadowRow[] {
  return SHADOW_KEYS.flatMap((key) => {
    const s = shadow.pairs?.[key];
    if (!s) return [];
    const ci = s.ci_low === null || s.ci_high === null ? "—" : `${fmtPct(s.ci_low)} ~ ${fmtPct(s.ci_high)}`;
    return [{ key, label: SHADOW_LABEL[key], n: s.n, mean: fmtPct(s.mean_diff), ci, note: s.n < shadow.min_pairs ? `표본 부족(n < ${shadow.min_pairs})` : "" }];
  });
}

/** 기준선이 전부 min_pairs 미만(또는 하나도 없음)이면 표 대신 한 줄 — 「기준선 n개 · 표본 x/min — min쌍부터 집계」. 하나라도 채웠으면 null. */
export function shadowSummary(shadow: Shadow): string | null {
  const stats = SHADOW_KEYS.flatMap((k) => (shadow.pairs?.[k] ? [shadow.pairs[k]!] : []));
  const best = Math.max(0, ...stats.map((s) => s.n));
  if (stats.length && best >= shadow.min_pairs) return null;
  return `기준선 ${stats.length}개 · 표본 ${best}/${shadow.min_pairs} — ${shadow.min_pairs}쌍부터 집계`;
}
