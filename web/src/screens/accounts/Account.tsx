// 계좌별 화면 — `/accounts/:id`. 돌아가기·머리(모의/실전·레인·모드+메모·현금 N초 전·NAV)·NAV 차트(30일)·보유(현물 lot 펼침,
// 파생 계좌는 「파생 보유」)·단타(합계 미포함)·오늘 주문·체결·리스크(현물만)·모드 전환.
// 섹션 컨테이너는 데이터 전에 바로 그린다. 파생 계좌는 보유 절 id 가 `fo-positions`(메뉴 앵커), 아니면 `positions` + 빈 hidden `fo-positions`.
// 계좌 단위 일시정지는 API 에 없어 그리지 않는다.
import { useState, type ReactNode } from "react";
import { Link, useParams } from "react-router-dom";
import { Card } from "../../components/Card";
import { Chart } from "../../components/Chart";
import { Chip } from "../../components/Chip";
import { Empty } from "../../components/Empty";
import { Table, type Column } from "../../components/Table";
import type { ApiError } from "../../lib/api";
import { now as clockNow } from "../../lib/clock";
import { agoText, fmtDate, fmtMoney, fmtTime, kstDate } from "../../lib/format";
import { useFoQuotes, useNow, useQuotes, useResource } from "../../lib/hooks";
import type { Merged, Order } from "../today/today.ts";
import type { Account as AccountRow } from "./accounts.ts";
import { ModeSwitch } from "./ModeSwitch";
import { FoPositionsTable, PositionsTable } from "./PositionsTable";
import { navSeries, splitLanes, sumEval, withLive, type LiveRow, type NavFoReport, type NavPerf, type PositionRow } from "./positions.ts";
import { RiskCard, type Risk } from "./RiskCard";

type OpenLot = { ticker: string; qty: number; price: string | number; bought_at: string };
type TaxLots = { open_lots: OpenLot[] };

const LANE_LABEL: Record<string, string> = { spot: "현물", scalp: "단타", fo: "파생" };
const MODE_LABEL: Record<string, string> = { auto: "자동", approve: "승인", off: "끔" };
const SIDE_LABEL: Record<string, string> = { BUY: "매수", SELL: "매도", buy: "매수", sell: "매도" };

function failed(what: string, e: ApiError) {
  return <Empty title={`${what}을(를) 불러오지 못했습니다`}>{e.message}</Empty>;
}

export function Account() {
  const { id = "" } = useParams();
  const enc = encodeURIComponent(id);
  const [today] = useState(() => kstDate(clockNow()));
  const now = useNow(1000);

  const accounts = useResource<AccountRow[]>("/accounts", undefined, ["accounts", "control"]);
  const account = accounts.data?.find((a) => a.id === id) ?? null;
  const ready = !!account;
  const fo = account?.lane === "fo";
  const market = account?.markets?.[0] ?? "KR";
  const markets = account?.markets ?? [];

  const positions = useResource<Merged<PositionRow>>(`/accounts/${enc}/positions`, undefined, ["positions", "fo", "scalp", "triggers"]);
  const orders = useResource<Merged<Order>>("/orders", { date: today, account_id: id }, ["orders", "fo", "scalp"]);
  const risk = useResource<Risk>("/positions/risk", { account_id: id, market }, ["positions"], { enabled: ready && !fo });
  const lotsKr = useResource<TaxLots>(`/accounts/${enc}/tax-lots`, { market: "KR" }, ["positions", "orders"], { enabled: ready && !fo && markets.includes("KR") });
  const lotsUs = useResource<TaxLots>(`/accounts/${enc}/tax-lots`, { market: "US" }, ["positions", "orders"], { enabled: ready && !fo && markets.includes("US") });
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
            {fmtDate(l.bought_at)} · {l.qty}주 · {fmtMoney(l.price, c)}
          </li>
        ))}
      </ul>
    );
  };

  const positionsBody = (body: ReactNode) =>
    positions.error ? failed("보유", positions.error) : !positions.data ? <Empty title="불러오는 중" /> : body;

  return (
    <Screen>
      <BackLink />
      {account ? <Header account={account} at={accounts.at} now={now} /> : <Empty title="불러오는 중" />}

      <section id="nav-chart" className="screen-section">
        <h2>NAV</h2>
        {!ready ? (
          <Empty title="불러오는 중" />
        ) : nav.error ? (
          failed("NAV", nav.error)
        ) : !nav.data ? (
          <Empty title="불러오는 중" />
        ) : navSeries(nav.data).length ? (
          <Chart series={navSeries(nav.data)} />
        ) : (
          <Empty title="NAV 기록 없음" />
        )}
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

      <section id="orders" className="screen-section">
        <h2>오늘 주문·체결</h2>
        {orders.error ? failed("주문", orders.error) : !orders.data ? <Empty title="불러오는 중" /> : <OrdersTable data={orders.data} />}
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
            <RiskCard risk={risk.data} />
          )}
        </section>
      )}

      <section id="mode" className="screen-section">
        <h2>모드</h2>
        {account ? (
          <ModeSwitch key={account.mode} id={account.id} env={account.env} mode={account.mode} modeNote={account.mode_note} onDone={accounts.reload} />
        ) : (
          <Empty title="불러오는 중" />
        )}
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

function Header({ account: a, at, now }: { account: AccountRow; at: number; now: Date }) {
  const c = a.currency === "USD" ? "USD" : "KRW";
  const fetched = at > 0 ? new Date(at).toISOString() : null;
  return (
    <Card
      title={a.id}
      right={
        <span className="chips">
          <Chip tone={a.env === "paper" ? "muted" : "live"}>{a.env === "paper" ? "모의" : "실전"}</Chip>
          <Chip>{LANE_LABEL[a.lane] ?? a.lane}</Chip>
        </span>
      }
    >
      <p className="account-mode">
        모드 {MODE_LABEL[a.mode] ?? a.mode}
        {a.mode_note ? <span className="account-mode-note"> · {a.mode_note}</span> : null}
      </p>
      <dl className="account-stats">
        <div>
          <dt>NAV</dt>
          <dd>{fmtMoney(a.nav, c)}</dd>
        </div>
        <div>
          <dt>현금</dt>
          <dd>
            현금 {fmtMoney(a.cash ?? null, c)} · {agoText(a.cash_at ?? fetched, now)}
          </dd>
        </div>
      </dl>
    </Card>
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
            평가 합계({c}) {s.eval} · 손익 {s.pnl}
          </span>
        );
      })}
    </p>
  );
}

type OrderRow = Order & { _k: string };

function OrdersTable({ data }: { data: Merged<Order> }) {
  const rows: OrderRow[] = data.items.map((o, i) => ({ ...o, _k: `${o.lane}-${o.id ?? i}` }));
  const columns: Column<OrderRow>[] = [
    { key: "at", label: "시각", width: "14%", render: (o) => fmtTime(o.at ?? null) },
    { key: "ticker", label: "종목", width: "20%", long: true, render: (o) => o.ticker ?? "—" },
    { key: "side", label: "방향", width: "10%", render: (o) => SIDE_LABEL[o.side ?? ""] ?? o.side ?? "—" },
    { key: "qty", label: "수량", width: "10%", align: "num", render: (o) => String(o.qty ?? "—") },
    { key: "filled", label: "체결", width: "10%", align: "num", render: (o) => String(o.filled_qty ?? 0) },
    { key: "price", label: "가격", width: "14%", align: "num", render: (o) => (o.price ?? o.order_price ?? "—").toString() },
    { key: "lane", label: "레인", width: "10%", align: "chip", render: (o) => <Chip>{LANE_LABEL[o.lane] ?? o.lane}</Chip> },
    { key: "status", label: "상태", width: "12%", align: "chip", render: (o) => <Chip>{o.status}</Chip> },
  ];
  return <Table columns={columns} rows={rows} rowKey={(o) => o._k} partial={data.partial} empty="오늘 주문 없음" />;
}
