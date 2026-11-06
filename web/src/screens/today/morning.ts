// 오늘 「아침 파이프라인」·「리스크 게이트」 순수 함수(파일명은 Pipeline.tsx 와 대소문자만 다른 이름을 피한 것).
// 응답 정본: `features/spot/decisions_view.py market_today`(tickers·names·scan·market_files·news_jobs), `risk_checks`.
import { fmtWhen } from "../../lib/format";
import { GLOSSARY } from "../../lib/glossary";
import { tickerLabel } from "../../lib/ticker";

export type MarketToday = {
  date: string;
  market: string;
  tickers: Record<string, string[]>;
  names?: Record<string, string | null>;
  scan: { picks: { ticker: string; reason: string | null; discovery: { tier: string | null; catalyst: string | null } | null }[]; targets: number; cap_hit: boolean } | null;
  market_files: string[];
  news_jobs: { id: number; kind: string; ticker: string | null; status: string }[];
};

export type PipelineStep = { id: "scan" | "market" | "analyze"; label: string; done: boolean };
export type PipelineRow = { ticker: string; label: string; last: string; count: number };
export type Pipeline = { steps: PipelineStep[]; rows: PipelineRow[]; decided: number; picks: string | null; newsJobs: number; capHit: boolean };

const STAGE_KEYS = Object.keys(GLOSSARY)
  .filter((k) => k.startsWith("stage."))
  .map((k) => k.slice("stage.".length))
  .sort((a, b) => b.length - a.length);

/** 체크포인트 이름(`20_debate_r0_bull`) → glossary `stage.*` 한글(가장 긴 접두 일치). 모르는 이름은 그대로. */
export function stageLabel(stage: string): string {
  const key = STAGE_KEYS.find((k) => stage === k || stage.startsWith(k + "_"));
  return key ? GLOSSARY[`stage.${key}`].label : stage;
}

/** 시장 하루의 파이프라인 — 스캔·시장 세션·분석 단계와 종목 행. 아무 기록도 없으면 null. */
export function pipelineOf(m: MarketToday): Pipeline | null {
  const tickers = Object.entries(m.tickers ?? {});
  const files = m.market_files ?? [];
  const jobs = m.news_jobs ?? [];
  if (!tickers.length && !m.scan && !files.length && !jobs.length) return null;
  const names = m.names ?? {};
  const rows = tickers.map(([ticker, stages]) => ({
    ticker,
    label: tickerLabel(ticker, names[ticker]),
    last: stages.length ? stageLabel(stages[stages.length - 1]) : "—",
    count: stages.length,
  }));
  const decided = tickers.filter(([, stages]) => stages.includes("30_decision")).length;
  const picks = m.scan?.picks?.length
    ? m.scan.picks
        .map((p) => {
          const cat = p.discovery?.catalyst;
          return `${tickerLabel(p.ticker, names[p.ticker])} ${p.reason ?? ""}${cat ? `(${cat})` : ""}`.trim();
        })
        .join(" · ")
    : null;
  return {
    steps: [
      { id: "scan", label: "스캔", done: !!m.scan || files.includes("_scan") },
      { id: "market", label: "시장 세션", done: files.includes("_market_view") },
      { id: "analyze", label: tickers.length ? `분석 ${tickers.length}종목 · 결정 ${decided}` : "분석", done: tickers.length > 0 },
    ],
    rows,
    decided,
    picks,
    newsJobs: jobs.length,
    capHit: !!m.scan?.cap_hit,
  };
}

export type RiskCheck = {
  id: number;
  decision_id: number | null;
  account_id: string;
  approved: boolean;
  adjusted_qty: number | null;
  reasons: string[];
  created_at: string;
  ticker?: string | null;
  name?: string | null;
  market?: string | null;
  date?: string | null;
  action?: string | null;
};
export type RiskCheckRow = {
  id: number;
  at: string;
  when: string;
  account_id: string;
  market: string | null;
  ticker: string;
  approved: boolean;
  adjusted: string;
  reasons: string;
  href: string | null;
};

export function riskCheckRows(checks: RiskCheck[], now: Date = new Date()): RiskCheckRow[] {
  return checks.map((c) => ({
    id: c.id,
    at: c.created_at,
    when: fmtWhen(c.created_at, now),
    account_id: c.account_id,
    market: c.market ?? null,
    ticker: c.ticker ? tickerLabel(c.ticker, c.name) : "—",
    approved: c.approved,
    adjusted: c.adjusted_qty === null || c.adjusted_qty === undefined ? "—" : `${c.adjusted_qty.toLocaleString("en-US")}주`,
    reasons: c.reasons?.length ? c.reasons.join(" · ") : "—",
    href: c.decision_id ? `/decisions/spot/${c.decision_id}` : null,
  }));
}
