// 시세·차트 — 시장 고르고 종목 검색(GET /names/search) → 고르면 GET /charts?days=120 의 일봉 캔들 + 체결 표식 + 손절·목표선
// + 실시간 현재가선(useQuotes). KR 은 호가 10단(GET /quotes/book)을 서버가 정한 next_poll_ms 마다 다시 읽는다
// (가려진 탭·언마운트면 멈춤). 색은 data.ts 가 준 CSS 변수 이름을 여기서 값으로 푼다.
import { useEffect, useMemo, useState } from "react";
import { Chart, type ChartCandle, type ChartLine, type ChartMarker } from "../../components/Chart";
import { Empty } from "../../components/Empty";
import { ApiError, errorText, getJson } from "../../lib/api";
import { fmtInt } from "../../lib/format";
import { useQuotes, useResource, useVisible } from "../../lib/hooks";
import { body, failed } from "../performance/ResourceView";
import { useMarket, type Market } from "./Data.tsx";
import { chartLines, chartMarkers, type FillMarker } from "./data.ts";

type NameHit = { market: string; ticker: string; name: string | null; exchange: string | null; source: string };
type Picked = { ticker: string; market: Market; name: string | null; exchange: string | null };
type Num = string | number;
type ChartsResp = {
  ticker: string;
  market: string;
  candles: { time: string; open: Num; high: Num; low: Num; close: Num; volume: number }[];
  markers: FillMarker[];
  lines: { stop_loss: Num | null; take_profit: Num | null; date: string | null } | null;
};
type BookResp = {
  session: string;
  ticker: string;
  book: { asks: [Num, Num][]; bids: [Num, Num][]; total_ask: Num; total_bid: Num; time: string; at: string; source: string } | null;
  next_poll_ms: number;
};

const MIN_POLL_MS = 1000;
const RETRY_MS = 30000;

function cssVar(name: string): string {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim() || name;
}

export function ChartView() {
  const [market] = useMarket();
  const [text, setText] = useState("");
  const [q, setQ] = useState("");
  useEffect(() => setQ(""), [market]); // 시장이 바뀌면 검색 결과는 뜻이 없다
  const [picked, setPicked] = useState<Picked | null>(null);
  const searchParams = useMemo(() => ({ q, market }), [q, market]);
  const search = useResource<NameHit[]>("/names/search", searchParams, [], { enabled: !!q });

  return (
    <>
      <form
        className="perf-filters"
        onSubmit={(e) => {
          e.preventDefault();
          setQ(text.trim());
        }}
      >
        <input aria-label="종목 검색" placeholder="종목명 또는 코드로 검색" value={text} onChange={(e) => setText(e.target.value)} />
        <button type="submit" className="btn">
          검색
        </button>
      </form>
      {q
        ? body(search, "종목 검색", (hits) =>
            hits.length ? (
              <ul className="data-hits">
                {hits.map((h) => (
                  <li key={`${h.market}:${h.ticker}`}>
                    <button
                      type="button"
                      className="btn"
                      aria-pressed={picked?.ticker === h.ticker && picked.market === h.market}
                      onClick={() => setPicked({ ticker: h.ticker, market: h.market === "US" ? "US" : "KR", name: h.name, exchange: h.exchange })}
                    >
                      {h.name ? (
                        <>
                          {h.name} <span className="data-muted">{h.ticker}</span>
                        </>
                      ) : (
                        h.ticker
                      )}
                    </button>
                  </li>
                ))}
              </ul>
            ) : (
              <Empty title="검색 결과 없음" />
            ),
          )
        : null}
      {picked ? <PickedChart key={`${picked.market}:${picked.ticker}`} picked={picked} /> : <Empty title="종목을 검색해 고르세요" />}
    </>
  );
}

function PickedChart({ picked }: { picked: Picked }) {
  const { ticker, market, exchange } = picked;
  const params = useMemo((): Record<string, string> => {
    const p: Record<string, string> = { ticker, market, days: "120" };
    if (exchange) p.exchange = exchange;
    return p;
  }, [ticker, market, exchange]);
  const charts = useResource<ChartsResp>("/charts", params, ["orders", "positions"]);
  const tickers = useMemo(() => [ticker], [ticker]);
  const quotes = useQuotes(market, tickers);
  const price = quotes?.quotes[ticker]?.price ?? null;

  const d = charts.data;
  const candles = useMemo(
    (): ChartCandle[] => (d?.candles ?? []).map((c) => ({ time: c.time, open: Number(c.open), high: Number(c.high), low: Number(c.low), close: Number(c.close) })),
    [d],
  );
  const markers = useMemo((): ChartMarker[] => chartMarkers(d?.markers ?? []).map((m) => ({ ...m, color: cssVar(m.color) })), [d]);
  const lines = useMemo((): ChartLine[] => {
    const l = d?.lines;
    const level = (v: Num | null | undefined) => (v === null || v === undefined ? null : { level: v, source: "chart" });
    const out = chartLines([{ ticker, stop: level(l?.stop_loss), target: level(l?.take_profit) }], ticker);
    const p = price === null ? NaN : Number(price);
    if (Number.isFinite(p)) out.push({ price: p, label: "현재가", color: "--accent" });
    return out.map((x) => ({ ...x, color: cssVar(x.color) }));
  }, [d, ticker, price]);

  return (
    <>
      <h3 className="perf-sub">
        {picked.name ?? ticker} <span className="data-muted">{ticker}</span>
      </h3>
      {body(charts, "차트", () => (candles.length ? <Chart candles={candles} markers={markers} lines={lines} height={320} /> : <Empty title="일봉 없음" />))}
      {market === "KR" ? <Book ticker={ticker} /> : null}
    </>
  );
}

/** 호가를 next_poll_ms 마다 읽는다. 가려지면 멈추고 보이면 즉시 다시, 언마운트·종목 변경이면 끊는다. */
function useBook(ticker: string): { data: BookResp | null; error: ApiError | null } {
  const visible = useVisible();
  const [state, setState] = useState<{ data: BookResp | null; error: ApiError | null }>({ data: null, error: null });
  useEffect(() => {
    if (!visible) return;
    let alive = true;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const ctrl = new AbortController();
    const run = () => {
      getJson<BookResp>("/quotes/book", { ticker }, { signal: ctrl.signal }).then(
        (r) => {
          if (!alive) return;
          setState({ data: r, error: null });
          timer = setTimeout(run, Math.max(MIN_POLL_MS, r.next_poll_ms));
        },
        (e: unknown) => {
          if (!alive || (e as { name?: string })?.name === "AbortError") return;
          setState((s) => ({ ...s, error: e instanceof ApiError ? e : new ApiError(0, errorText(e)) }));
          timer = setTimeout(run, RETRY_MS);
        },
      );
    };
    run();
    return () => {
      alive = false;
      clearTimeout(timer);
      ctrl.abort();
    };
  }, [ticker, visible]);
  return state;
}

function Book({ ticker }: { ticker: string }) {
  const { data, error } = useBook(ticker);
  let content;
  if (error && !data) content = failed("호가", error);
  else if (!data) content = <Empty title="불러오는 중" />;
  else if (!data.book) content = <Empty title={data.session === "open" ? "호가 없음" : "장 마감 — 호가 없음"} />;
  else {
    const asks = data.book.asks.slice(0, 10).reverse();
    const bids = data.book.bids.slice(0, 10);
    content = (
      <div className="table-scroll">
        <table className="table data-book">
          <thead>
            <tr>
              <th scope="col" className="cell-num">
                매도잔량
              </th>
              <th scope="col" className="cell-num">
                가격
              </th>
              <th scope="col" className="cell-num">
                매수잔량
              </th>
            </tr>
          </thead>
          <tbody>
            {asks.map(([p, qty], i) => (
              <tr key={`a${i}`} data-side="ask">
                <td className="cell-num">{fmtInt(qty)}</td>
                <td className="cell-num">{fmtInt(p)}</td>
                <td />
              </tr>
            ))}
            {bids.map(([p, qty], i) => (
              <tr key={`b${i}`} data-side="bid">
                <td />
                <td className="cell-num">{fmtInt(p)}</td>
                <td className="cell-num">{fmtInt(qty)}</td>
              </tr>
            ))}
            <tr>
              <td className="cell-num">{fmtInt(data.book.total_ask)}</td>
              <td className="cell-num data-muted">합계</td>
              <td className="cell-num">{fmtInt(data.book.total_bid)}</td>
            </tr>
          </tbody>
        </table>
        {error ? <p className="data-error" role="alert">{errorText(error)}</p> : null}
      </div>
    );
  }
  return (
    <>
      <h3 className="perf-sub">호가 10단</h3>
      {content}
    </>
  );
}
