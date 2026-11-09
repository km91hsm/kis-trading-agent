// 계좌별 화면 — `/accounts/:id`. 돌아가기·h1·머리 카드(모의/실전·레인 칩, Stats NAV·현금(갱신 시각), 모드 전환) →
// 오늘 주문·체결(CSV) → 보유(현물 lot 펼침, 파생 계좌는 「파생 보유」) → 단타(합계 미포함) → 리스크(현물만) →
// 세금 로트(현물만: 연도 선택 → `/accounts/{id}/tax-lots?market&year`, 시장별 요약 Stats·실현 로트 표·CSV) → NAV 차트(30일).
// 섹션 컨테이너는 데이터 전에 바로 그린다. 파생 계좌는 보유 절 id 가 `fo-positions`(메뉴 앵커), 아니면 `positions` + 빈 hidden `fo-positions`.
// 계좌 단위 일시정지는 API 에 없어 그리지 않는다.
import { useState, type ReactNode } from "react";
import { Link, useParams } from "react-router-dom";
import { Card } from "../../components/Card";
import { Chart } from "../../components/Chart";
import { Chip } from "../../components/Chip";
import { Empty } from "../../components/Empty";
import { Stats } from "../../components/Stats";
import { Table, type Column } from "../../components/Table";
import { csvUrl, type ApiError } from "../../lib/api";
import { agoText, fmtDate, fmtInt, fmtMoney, fmtPt, fmtTime } from "../../lib/format";
import { tickerLabel } from "../../lib/ticker";
import { useFoQuotes, useKstDate, useNow, useQuotes, useResource } from "../../lib/hooks";
import { statusLabel, type Merged, type Order } from "../today/today.ts";
import type { Account as AccountRow } from "./accounts.ts";
import { ModeSwitch } from "./ModeSwitch";
import { FoPositionsTable, PositionsTable } from "./PositionsTable";
import { navSeries, splitLanes, sumEval, withLive, type LiveRow, type NavFoReport, type NavPerf, type PositionRow } from "./positions.ts";
import { RiskCard, type Risk } from "./RiskCard";

type OpenLot = { ticker: string; qty: number; price: string | number; bought_at: string };
type RealizedLot = { ticker: string; qty: number; buy_date: string; sell_date: string; cost: string; proceeds: string; pnl: string; term: string; wash_sale: boolean };
type TaxSummary = { realized_pnl: string; short_pnl: string; long_pnl: string; wash_sales: number; unmatched_sell_qty: number };
type TaxLots = { open_lots: OpenLot[]; realized: RealizedLot[]; summary: TaxSummary; year: number | null };

const LANE_LABEL: Record<string, string> = { spot: "현물", scalp: "단타", fo: "파생" };
const SIDE_LABEL: Record<string, string> = { BUY: "매수", SELL: "매도", buy: "매수", sell: "매도" };

function failed(what: string, e: ApiError) {
  return <Empty title={`${what}을(를) 불러오지 못했습니다`}>{e.message}</Empty>;
}

export function Account() {
  const { id = "" } = useParams();
  const enc = encodeURIComponent(id);
  const today = useKstDate();
  const now = useNow(1000);

  const accounts = useResource<AccountRow[]>("/accounts", undefined, ["accounts", "control"]);
  const account = accounts.data?.find((a) => a.id === id) ?? null;
  const ready = !!account;
  const fo = account?.lane === "fo";
  const market = account?.markets?.[0] ?? "KR";
  const markets = account?.markets ?? [];

  const positions = useResource<Merged<PositionRow>>(`/accounts/${enc}/positions`, undefined, ["positions", "fo", "scalp", "triggers"]);
  const orderParams = { date: today, account_id: id };
  const orders = useResource<Merged<Order>>("/orders", orderParams, ["orders", "fo", "scalp"]);
  const risk = useResource<Risk>("/positions/risk", { account_id: id, market }, ["positions"], { enabled: ready && !fo });
  // 세금 로트 연도 — 기본 올해(KST), "" 는 전체. 보유 로트(open_lots)는 연도와 무관하게 전부 온다.
  const [year, setYear] = useState(today.slice(0, 4));
  const lotParams = (m: string): Record<string, string> => (year ? { market: m, year } : { market: m });
  const lotsKr = useResource<TaxLots>(`/accounts/${enc}/tax-lots`, lotParams("KR"), ["positions", "orders"], { enabled: ready && !fo && markets.includes("KR") });
  const lotsUs = useResource<TaxLots>(`/accounts/${enc}/tax-lots`, lotParams("US"), ["positions", "orders"], { enabled: ready && !fo && markets.includes("US") });
  const perf = useResource<NavPerf>("/analytics/performance", { account_id: id, market, days: "30" }, ["accounts"], { enabled: ready && !fo });
  const foReport = useResource<NavFoReport>("/analytics/fo/report", { account_id: id, days: "30" }, ["accounts"], { enabled: ready && fo });

  const lanes = splitLanes(positions.data?.items ?? []);
  const shown = [...lanes.spot, ...lanes.scalp];
  const krQuotes = useQuotes("KR", shown.filter((r) => r.market === "KR").map((r) => r.ticker));
  const usQuotes = useQuotes("US", shown.filter((r) => r.market === "US").map((r) => r.ticker));
  const foQuotes = useFoQuotes(lanes.fo.map((r) => r.ticker));
  const t = now.getTime();
  const quotes = { quotes: { ...(krQuotes?.quotes ?? {}), ...(usQuotes?.quotes ?? {}) } };
  const spot = withLive(lanes.spot, quotes, t);
  const scalp = withLive(lanes.scalp, quotes, t);
  const foRows = withLive(lanes.fo, foQuotes, t);

  if (accounts.error) {
    return (
      <Screen>
        <BackLink />
        {failed("계좌", accounts.error)}
      </Screen>
    );
  }
  if (accounts.data && !account) {
    return (
      <Screen>
        <BackLink />
        <Empty title="계좌 없음">{id}</Empty>
      </Screen>
    );
  }

  const nav = fo ? foReport : perf;
  const lotsOf = (r: LiveRow<PositionRow>) => {
    const res = r.market === "US" ? lotsUs : lotsKr;
    if (res.error) return failed("lot", res.error);
    if (!res.data) return <Empty title="불러오는 중" />;
    const lots = res.data.open_lots.filter((l) => l.ticker === r.ticker);
    if (!lots.length) return <Empty title="lot 없음" />;
    const c = r.currency === "USD" ? "USD" : "KRW";
    return (
      <ul className="lot-list">
        {lots.map((l, i) => (
          <li key={`${l.bought_at}-${i}`}>
            {fmtDate(l.bought_at)} · {fmtInt(l.qty)}주 · {fmtMoney(l.price, c)}
          </li>
        ))}
      </ul>
    );
  };

  const positionsBody = (body: ReactNode) =>
    positions.error ? failed("보유", positions.error) : !positions.data ? <Empty title="불러오는 중" /> : body;

  const names: Record<string, string | null | undefined> = {};
  for (const r of positions.data?.items ?? []) names[r.ticker] = r.name;

  const navChart = !ready ? (
    <Empty title="불러오는 중" />
  ) : nav.error ? (
    failed("NAV", nav.error)
  ) : !nav.data ? (
    <Empty title="불러오는 중" />
  ) : navSeries(nav.data).length ? (
    <Chart series={navSeries(nav.data)} />
  ) : (
    <Empty title="NAV 기록 없음" />
  );

  return (
    <Screen>
      <BackLink />
      <h1 className="screen-title">계좌 {id}</h1>
      {account ? <Header account={account} at={accounts.at} now={now} onDone={accounts.reload} /> : <Empty title="불러오는 중" />}

      <section id="orders" className="screen-section">
        <h2>오늘 주문·체결</h2>
        {orders.error ? failed("주문", orders.error) : !orders.data ? <Empty title="불러오는 중" /> : <OrdersTable data={orders.data} csvHref={orders.data.items.length ? csvUrl("/orders", orderParams) : undefined} />}
      </section>

      {fo ? (
        <section id="fo-positions" className="screen-section">
          <h2>파생 보유</h2>
          {positionsBody(<FoPositionsTable rows={foRows} partial={positions.data?.partial} />)}
        </section>
      ) : (
        <>
          <section id="positions" className="screen-section">
            <h2>보유</h2>
            {positionsBody(
              <>
                <PositionsTable rows={spot} partial={positions.data?.partial} empty="보유 종목 없음" expand={lotsOf} />
                <Totals rows={spot} />
              </>,
            )}
          </section>
          <section id="fo-positions" className="screen-section" hidden />
        </>
      )}

      <section id="scalp-positions" className="screen-section" hidden={!scalp.length}>
        <h2>단타</h2>
        {scalp.length ? <PositionsTable rows={scalp} empty="단타 보유 없음" /> : null}
      </section>

      {fo ? null : (
        <section id="risk" className="screen-section">
          <h2>리스크</h2>
          {!ready ? (
            <Empty title="불러오는 중" />
          ) : risk.error ? (
            failed("리스크", risk.error)
          ) : !risk.data ? (
            <Empty title="불러오는 중" />
          ) : (
            <RiskCard risk={risk.data} names={names} />
          )}
        </section>
      )}

      {fo ? null : (
        <section id="tax-lots" className="screen-section">
          <h2>세금 로트</h2>
          <p className="perf-filters">
            <label>
              연도{" "}
              <select aria-label="연도" value={year} onChange={(e) => setYear(e.target.value)}>
                {yearOptions(today).map((y) => (
                  <option key={y} value={y}>
                    {y || "전체"}
                  </option>
                ))}
              </select>
            </label>
            <span className="data-muted">FIFO 실현 로트·워시세일(표시 전용)</span>
          </p>
          {!ready ? (
            <Empty title="불러오는 중" />
          ) : (
            markets.map((m) => (
              <TaxBlock key={m} market={m} res={m === "US" ? lotsUs : lotsKr} names={names} csvHref={csvUrl(`/accounts/${enc}/tax-lots`, lotParams(m))} single={markets.length === 1} />
            ))
          )}
        </section>
      )}

      <section id="nav-chart" className="screen-section">
        <h2>NAV 추이</h2>
        {navChart}
      </section>
    </Screen>
  );
}

function Screen({ children }: { children: ReactNode }) {
  return (
    <div className="screen" data-screen="accounts">
      {children}
    </div>
  );
}

function BackLink() {
  return (
    <Link to="/accounts" className="back-link">
      ‹ 돌아가기
    </Link>
  );
}

function Header({ account: a, at, now, onDone }: { account: AccountRow; at: number; now: Date; onDone(): void }) {
  const c = a.currency === "USD" ? "USD" : "KRW";
  const settled = a.cash_available !== undefined && a.cash_available !== null;
  const fetched = at > 0 ? new Date(at).toISOString() : null;
  return (
    <div className="account-head">
      <Card
        title={a.id}
        right={
          <span className="chips">
            <Chip tone={a.env === "paper" ? "muted" : "live"}>{a.env === "paper" ? "모의" : "실전"}</Chip>
            <Chip>{LANE_LABEL[a.lane] ?? a.lane}</Chip>
          </span>
        }
      >
        <Stats
          cols={settled ? 3 : 2}
          items={[
            { id: "nav", label: "NAV", value: fmtMoney(a.nav, c) },
            { id: "cash", label: settled ? "예수금(결제 전)" : "현금", value: fmtMoney(a.cash ?? null, c), sub: `갱신 ${agoText(a.cash_at ?? fetched, now)}` },
            ...(settled ? [{ id: "cash_available", label: "주문 가능", value: fmtMoney(a.cash_available ?? null, c) }] : []),
          ]}
        />
        {a.paused ? <p className="mode-note">(전체 일시정지로 새 매수 멈춤)</p> : null}
        <ModeSwitch key={`${a.id}:${a.mode}`} id={a.id} env={a.env} mode={a.mode} modeNote={a.mode_note} onDone={onDone} />
      </Card>
    </div>
  );
}

/** 통화별 평가 합계(단타 제외는 sumEval 이 한다 — 여기 넘기는 행은 현물만). */
function Totals({ rows }: { rows: LiveRow<PositionRow>[] }) {
  const currencies = Array.from(new Set(rows.map((r) => r.currency)));
  if (!currencies.length) return null;
  return (
    <p className="positions-total">
      {currencies.map((c) => {
        const s = sumEval(rows.filter((r) => r.currency === c));
        return (
          <span key={c}>
            매수 합계({c}) {s.cost} · 평가 합계({c}) {s.eval} · 손익 {s.pnl}
          </span>
        );
      })}
    </p>
  );
}

/** 연도 선택지 — 올해부터 3년 + 전체(""). */
function yearOptions(today: string): string[] {
  const y = Number(today.slice(0, 4));
  return [String(y), String(y - 1), String(y - 2), ""];
}

const TERM_LABEL: Record<string, string> = { short: "단기", long: "장기" };

function TaxBlock({ market, res, names, csvHref, single }: { market: string; res: ReturnType<typeof useResource<TaxLots>>; names: Record<string, string | null | undefined>; csvHref: string; single: boolean }) {
  const c = market === "US" ? "USD" : "KRW";
  const columns: Column<RealizedLot & { _k: string }>[] = [
    { key: "ticker", label: "종목", width: "20%", long: true, render: (r) => tickerLabel(r.ticker, names[r.ticker]) },
    { key: "qty", label: "수량", width: "8%", align: "num", render: (r) => `${fmtInt(r.qty)}주` },
    { key: "buy_date", label: "매수일", width: "11%", render: (r) => fmtDate(r.buy_date) },
    { key: "sell_date", label: "매도일", width: "11%", render: (r) => fmtDate(r.sell_date) },
    { key: "cost", label: "원가", width: "12%", align: "num", render: (r) => fmtMoney(r.cost, c) },
    { key: "proceeds", label: "매도금", width: "12%", align: "num", render: (r) => fmtMoney(r.proceeds, c) },
    { key: "pnl", label: "손익", width: "10%", align: "num", render: (r) => fmtMoney(r.pnl, c) },
    { key: "term", label: "기간", width: "8%", align: "chip", render: (r) => <Chip>{TERM_LABEL[r.term] ?? r.term}</Chip> },
    { key: "wash_sale", label: "워시세일", width: "8%", align: "chip", render: (r) => (r.wash_sale ? <Chip tone="warn">워시세일</Chip> : "—") },
  ];
  return (
    <div data-tax-market={market} className="tax-block">
      {single ? null : <h3 className="perf-sub">{market}</h3>}
      {res.error ? (
        failed("세금 로트", res.error)
      ) : !res.data ? (
        <Empty title="불러오는 중" />
      ) : (
        <>
          <Stats
            cols={4}
            items={[
              { label: "실현 손익", value: fmtMoney(res.data.summary.realized_pnl, c) },
              { label: "단기", value: fmtMoney(res.data.summary.short_pnl, c) },
              { label: "장기", value: fmtMoney(res.data.summary.long_pnl, c) },
              { label: "워시세일", value: `${fmtInt(res.data.summary.wash_sales)}건` },
            ]}
          />
          <Table columns={columns} rows={res.data.realized.map((r, i) => ({ ...r, _k: `${r.ticker}-${r.sell_date}-${i}` }))} rowKey={(r) => r._k} csvHref={csvHref} empty="실현 로트 없음" />
        </>
      )}
    </div>
  );
}

type OrderRow = Order & { _k: string };

const FAILED_ORDER = new Set(["failed", "rejected", "cancelled"]);
/** 주문 상태 칩 색 — 체결은 ok, 실패·거절·취소는 fail, 나머지(접수·부분) muted. */
function orderTone(status: string): "ok" | "fail" | "muted" {
  if (status === "filled") return "ok";
  return FAILED_ORDER.has(status) ? "fail" : "muted";
}

/** 주문 가격 — 파생은 지수 포인트, 현물·단타는 주문 시장 통화(US → USD). */
function priceText(o: Order): string {
  const v = o.price ?? o.order_price;
  if (o.lane === "fo" || o.asset === "fo") return fmtPt(v);
  return fmtMoney(v, o.market === "US" ? "USD" : "KRW");
}

function OrdersTable({ data, csvHref }: { data: Merged<Order>; csvHref?: string }) {
  const rows: OrderRow[] = data.items.map((o, i) => ({ ...o, _k: `${o.lane}-${o.id ?? i}` }));
  const columns: Column<OrderRow>[] = [
    { key: "at", label: "시각", width: "14%", render: (o) => fmtTime(o.at ?? null) },
    { key: "ticker", label: "종목", width: "20%", long: true, render: (o) => tickerLabel(o.ticker, o.name) },
    { key: "side", label: "방향", width: "10%", render: (o) => SIDE_LABEL[o.side ?? ""] ?? o.side ?? "—" },
    { key: "qty", label: "수량", width: "10%", align: "num", render: (o) => fmtInt(o.qty) },
    { key: "filled", label: "체결", width: "10%", align: "num", render: (o) => fmtInt(o.filled_qty ?? 0) },
    { key: "price", label: "가격", width: "14%", align: "num", render: (o) => priceText(o) },
    { key: "lane", label: "레인", width: "10%", align: "chip", render: (o) => <Chip>{LANE_LABEL[o.lane] ?? o.lane}</Chip> },
    { key: "status", label: "상태", width: "12%", align: "chip", render: (o) => <Chip tone={orderTone(o.status)}>{statusLabel(o.status)}</Chip> },
  ];
  return <Table columns={columns} rows={rows} rowKey={(o) => o._k} partial={data.partial} csvHref={csvHref} empty="오늘 주문 없음" />;
}
