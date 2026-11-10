// 「데이터」 화면 순수 함수 — 뉴스 행(종목 이름 칩·촉매·테마·「판정에 쓰임」), 테마 행, 지표 표(옛 `pages/features.ts` 의
// sortRows·fmt·KEY_COLUMNS 이식), 차트 체결 표식·손절/목표선. 색은 CSS 변수 이름(--up 등)으로 두고 화면이 풀어 쓴다.
// 응답 정본: ingest/routes.py·news_store.py, scan/routes.py·theme.py, ml/routes.py, quotes/charts.py, spot/routes.py.
import type { ChartLine, ChartMarker } from "../../components/Chart";

// ── 뉴스 ─────────────────────────────────────────────────────────────────────
export type NewsItem = {
  id: number;
  market: string;
  source: string;
  query?: string | null;
  title: string;
  url: string | null;
  published_at: string | null;
  received_at: string;
  summary?: string | null;
  tickers: string[];
  lang?: string | null;
};
export type NewsResp = { market: string; hours: number; total: number; asof: string | null; items: NewsItem[] };
export type NewsLabels = Record<string, { catalyst: boolean; themes: string[] }>;
/** news_id → 그 뉴스를 쓴 현물 판정 id 들. */
export type NewsRefs = Record<string, number[]>;
/** "<시장>:<종목>" → 이름(모르는 종목은 키 없음). */
export type Names = Record<string, string>;

export type NewsRow = {
  id: number;
  title: string;
  url: string | null;
  source: string;
  at: string;
  tickers: { ticker: string; name: string }[];
  chips: string[];
  decisionId: number | null;
};

export const USED_CHIP = "판정에 쓰임";

/** 출처 원문(naver·google_rss·dart…) → 한글. 모르는 출처는 원문. */
const SOURCE_LABEL: Record<string, string> = {
  naver: "네이버",
  google_rss: "Google 뉴스 RSS",
  google: "Google 뉴스",
  manual: "수동 입력",
  dart: "전자공시(DART)",
  edgar: "EDGAR",
  kind: "KIND",
  saveticker: "세이브티커",
  blog: "블로그",
};
export const sourceLabel = (s: string | null | undefined): string => {
  if (!s) return "—";
  // 세이브티커는 `saveticker:<발행처>`(로이터·속보 등)로 쌓인다
  if (s.startsWith("saveticker:")) return `세이브티커 · ${s.slice("saveticker:".length)}`;
  return SOURCE_LABEL[s] ?? s;
};

/** 뉴스 화면 출처 필터 — API `source` 묶음 이름과 같다(전체는 값 없음). */
export const NEWS_SOURCES: { key: string | null; label: string }[] = [
  { key: null, label: "전체" },
  { key: "saveticker", label: "세이브티커" },
  { key: "naver", label: "네이버" },
  { key: "google_rss", label: "구글" },
  { key: "disclosure", label: "공시" },
];

/** 공시 중요도 → 한글 라벨·칩 색. 값이 없으면 null(칩을 그리지 않는다). */
const SEVERITY: Record<string, { label: string; tone: "fail" | "warn" | "muted" }> = {
  critical: { label: "긴급", tone: "fail" },
  high: { label: "중요", tone: "warn" },
  medium: { label: "보통", tone: "muted" },
  low: { label: "낮음", tone: "muted" },
};
export function severityChip(s: string | null | undefined): { label: string; tone: "fail" | "warn" | "muted" } | null {
  if (!s) return null;
  return SEVERITY[s] ?? { label: s, tone: "muted" };
}

export function newsRows(news: NewsItem[], labels: NewsLabels, refs: NewsRefs, names: Names): NewsRow[] {
  return news.map((n) => {
    const label = labels[String(n.id)];
    const ref = refs[String(n.id)] ?? [];
    const chips: string[] = [];
    if (label?.catalyst) chips.push("촉매");
    chips.push(...(label?.themes ?? []));
    if (ref.length) chips.push(USED_CHIP);
    return {
      id: n.id,
      title: n.title,
      url: n.url,
      source: n.source,
      at: n.published_at ?? n.received_at,
      tickers: n.tickers.map((t) => ({ ticker: t, name: names[`${n.market}:${t}`] ?? t })),
      chips,
      decisionId: ref[0] ?? null,
    };
  });
}

// ── 테마 ─────────────────────────────────────────────────────────────────────
export type ThemesResp = {
  market: string;
  date: string;
  observed_at: string | null;
  top_n: number;
  themes: {
    theme: string;
    score: number;
    components?: unknown;
    tickers: { ticker: string; name: string | null; why: string[] }[];
    members: string[];
    catalyst_ok: boolean;
    catalyst_ids?: number[];
  }[];
};
export type ThemeRow = { theme: string; score: number; tickers: { ticker: string; name: string; why: string[] }[]; members: number; catalystOk: boolean };

export function themeRows(themes: ThemesResp): ThemeRow[] {
  return themes.themes.map((t) => ({
    theme: t.theme,
    score: t.score,
    tickers: t.tickers.map((x) => ({ ticker: x.ticker, name: x.name ?? x.ticker, why: x.why ?? [] })),
    members: t.members.length,
    catalystOk: t.catalyst_ok,
  }));
}

// ── 지표 (옛 pages/features.ts 이식) ─────────────────────────────────────────
export type Cell = string | number | boolean | null;
export type FeatureRow = Record<string, Cell>;
export type LatestResp = { market: string; asof: string | null; columns: string[]; rows: FeatureRow[] };
export type HistoryResp = { ticker: string; market: string; rows: FeatureRow[] };
export type SortDir = "asc" | "desc";

/** 최신 표에 보여 줄 열(전부 보이면 너무 넓다). 이력 표는 같은 열을 날짜와 함께 쓴다. */
export const KEY_COLUMNS = [
  "ticker",
  "sector",
  "close",
  "roc_10",
  "high_52w_pos",
  "op_margin",
  "roe",
  "debt_ratio",
  "op_margin_vs_sector",
  "roe_vs_sector",
  "debt_ratio_vs_sector",
  "flow_foreign_20d",
  "news_count",
];

/** 지표 열 한글 라벨(`th[data-key]` 는 원문 키를 유지한다). 모르는 열은 원문. */
const FEATURE_LABEL: Record<string, string> = {
  ticker: "종목",
  sector: "업종",
  date: "날짜",
  close: "종가",
  volume: "거래량",
  roc_10: "10일 수익률",
  high_52w_pos: "52주 고점 위치",
  op_margin: "영업이익률",
  roe: "ROE",
  debt_ratio: "부채비율",
  op_margin_vs_sector: "영업이익률(업종 대비)",
  roe_vs_sector: "ROE(업종 대비)",
  debt_ratio_vs_sector: "부채비율(업종 대비)",
  flow_foreign_20d: "외국인 20일 순매수",
  flow_inst_20d: "기관 20일 순매수",
  flow_indiv_20d: "개인 20일 순매수",
  news_count: "뉴스 수",
  community_count: "커뮤니티 수",
};
export const featureLabel = (c: string): string => FEATURE_LABEL[c] ?? c;

/** 지표 값 형식 — 비율 열은 %, 가격·건수는 천 단위 정수, 순매수 같은 수치는 천 단위(소수 ≤2). 모르는 열은 fmt. */
const PCT_COLS = new Set(["roc_10", "high_52w_pos", "op_margin", "roe", "debt_ratio", "op_margin_vs_sector", "roe_vs_sector", "debt_ratio_vs_sector"]);
const INT_COLS = new Set(["volume", "news_count", "community_count"]);
export function fmtFeature(col: string, v: Cell | undefined): string {
  if (v == null) return "—";
  if (typeof v !== "number") return String(v);
  if (PCT_COLS.has(col)) return `${(v * 100).toFixed(2)}%`;
  if (col === "close") return v.toLocaleString("en-US", { maximumFractionDigits: 2 }); // KR 정수는 그대로, US 는 센트까지
  if (INT_COLS.has(col)) return Math.round(v).toLocaleString("en-US");
  if (col.startsWith("flow_")) return v.toLocaleString("en-US", { maximumFractionDigits: 2 });
  return fmt(v);
}

/** key 열로 정렬한 새 배열. null 은 방향과 무관하게 항상 마지막(결측이 '최대' 로 보이지 않게). */
export function sortRows<T extends FeatureRow>(rows: T[], key: string, dir: SortDir): T[] {
  const sign = dir === "asc" ? 1 : -1;
  return [...rows].sort((a, b) => {
    const x = a[key];
    const y = b[key];
    if (x == null && y == null) return 0;
    if (x == null) return 1;
    if (y == null) return -1;
    if (typeof x === "number" && typeof y === "number") return (x - y) * sign;
    return String(x).localeCompare(String(y)) * sign;
  });
}

/** 숫자는 소수 4자리(정수는 그대로), null 은 "—"(이 프로젝트 표기), 그 밖은 문자열. */
export function fmt(v: Cell | undefined): string {
  if (v == null) return "—";
  if (typeof v === "number") return Number.isInteger(v) ? String(v) : v.toFixed(4);
  return String(v);
}

function pick(row: FeatureRow, cols: string[]): FeatureRow {
  const out: FeatureRow = {};
  for (const c of cols) out[c] = row[c] ?? null;
  return out;
}

/** 최신 지표 행을 KEY_COLUMNS 만 남겨 정렬한다(sortKey 가 null 이면 응답 순서). */
export function featureRows(latest: LatestResp, sortKey: string | null, dir: SortDir): FeatureRow[] {
  const rows = latest.rows.map((r) => pick(r, KEY_COLUMNS));
  return sortKey ? sortRows(rows, sortKey, dir) : rows;
}

// ── 차트 ─────────────────────────────────────────────────────────────────────
/** `/charts` markers 한 항목(체결). side 는 buy|sell(대소문자 무관). */
export type FillMarker = { time: string | number; kind?: string; side: string; text: string };

/** 체결 표식 — 매수 --up(적)·매도 --down(청). color 는 CSS 변수 이름(화면이 값으로 푼다). */
export function chartMarkers(markers: FillMarker[]): ChartMarker[] {
  return markers.map((mk) => ({ time: mk.time, text: mk.text, color: mk.side.toLowerCase() === "buy" ? "--up" : "--down" }));
}

type Level = { level: string | number | null; source?: string } | null;
export type LevelRow = { ticker: string; stop: Level; target: Level };

/** 그 종목 보유 행의 손절(--fail)·목표(--ok) 가격선, 최대 2개(값 없는 쪽은 건너뜀). */
export function chartLines(positions: LevelRow[], ticker: string): ChartLine[] {
  const p = positions.find((r) => r.ticker === ticker);
  if (!p) return [];
  const out: ChartLine[] = [];
  const add = (lv: Level, label: string, color: string) => {
    const n = lv?.level === null || lv?.level === undefined || lv.level === "" ? NaN : Number(lv.level);
    if (Number.isFinite(n)) out.push({ price: n, label, color });
  };
  add(p.stop, "손절", "--fail");
  add(p.target, "목표", "--ok");
  return out;
}

/** 잔고·주문의 거래소 코드를 시세 API 코드로 바꾼다. 확인한 동일 거래소 별칭만 허용한다. */
export function quoteExchange(exchange: string | null): string | null {
  switch (exchange) {
    case "NAS": case "NASD": return "NAS";
    case "NYS": case "NYSE": return "NYS";
    case "AMS": case "AMEX": return "AMS";
    default: return null;
  }
}
