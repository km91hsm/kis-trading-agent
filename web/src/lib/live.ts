// 옛 `web/src/live.ts` 이식
// 장중 실시간 시세: 보이는 종목만 GET /quotes 로 서버가 정한 주기(next_poll_ms)마다 읽어 표의 현재가·손익을 갱신한다.
// 서버가 종목별 캐시·KIS 레이트리미터 안에서 부르므로 탭이 여럿이어도 KIS 호출은 늘지 않는다. 가려진 탭은 쉰다.
// change_pct 는 비율(0.0153 = 1.53%), applyLive 의 pnl_pct 는 퍼센트(10 = 10%).

export type QuoteRow = { price: string; change_pct: number | null; at: string; stale: boolean; source: "ws" | "rest" | "snapshot" };
export type QuotesResp = {
  session: string;
  at: string;
  quotes: Record<string, QuoteRow>;
  source: "ws" | "rest";
  stream?: boolean;
  hub_yield?: string | null;
  next_poll_ms: number;
};

export type FoQuoteRow = {
  kind: string;
  price: string;
  change_pct: number | null;
  bid: string | null;
  ask: string | null;
  at: string;
  stale: boolean;
  source: string;
};
export type FoQuotesResp = {
  session: string;
  at: string;
  quotes: Record<string, FoQuoteRow>;
  kospi200: { price: string; at: string | null; source: string } | null;
  next_poll_ms: number;
};

/** 이 안에 새 응답이 없으면 이전 시세를 '실시간' 으로 두지 않는다. */
export const QUOTE_TTL_MS = 30000;

/** 시세 요청 실패·TTL 초과: 모든 시세를 stale 로 내린다(표가 스냅샷 값으로 돌아간다). 원본은 그대로. */
export function degradeQuotes<R extends { quotes: Record<string, { stale: boolean }> }>(resp: R): R {
  const quotes: Record<string, { stale: boolean }> = {};
  for (const [t, q] of Object.entries(resp.quotes)) quotes[t] = { ...q, stale: true };
  return { ...resp, quotes } as R;
}

/** 다음 조회까지 대기(ms). 가려진 탭이면 null(멈춤), 아니면 서버가 정한 next_poll_ms. */
export function nextDelay(resp: { next_poll_ms: number }, hidden: boolean): number | null {
  return hidden ? null : resp.next_poll_ms;
}

/** stale 표시이거나, 시각이 깨졌거나, QUOTE_TTL_MS 넘게 지난 시세. */
export function isStale(row: { stale: boolean; at: string }, now: number): boolean {
  if (row.stale) return true;
  const t = Date.parse(row.at);
  return !Number.isFinite(t) || now - t > QUOTE_TTL_MS;
}

type Num = number | string | null | undefined;
export type LiveInput = { ticker: string; qty: Num; avg_price: Num; last_price: Num };
export type LiveFields = { current_price: number | null; pnl: number | null; pnl_pct: number | null; live: boolean };

const num = (v: Num): number | null => {
  if (v === null || v === undefined || v === "") return null;
  const n = Number(v);
  return Number.isFinite(n) ? n : null;
};

/** 보유 행에 실시간 현재가를 입힌다. stale·스냅샷 출처 시세는 쓰지 않고 last_price 로 둔다(live false).
 *  pnl = (현재가 − 평단) × 수량, pnl_pct = pnl / (평단 × 수량) × 100(평단·수량이 0 이면 null). */
export function applyLive<R extends LiveInput>(rows: R[], quotes: Record<string, QuoteRow> | null | undefined): (R & LiveFields)[] {
  return rows.map((r) => {
    const q = quotes?.[r.ticker];
    const qp = q && !q.stale && q.source !== "snapshot" ? num(q.price) : null;
    const live = qp !== null;
    const price = live ? qp : num(r.last_price);
    const qty = num(r.qty) ?? 0;
    const avg = num(r.avg_price);
    const pnl = price === null || avg === null ? null : (price - avg) * qty;
    const cost = avg === null ? 0 : avg * qty;
    const pnl_pct = pnl === null || !(cost > 0) ? null : (pnl / cost) * 100;
    return { ...r, current_price: price, pnl, pnl_pct, live };
  });
}
