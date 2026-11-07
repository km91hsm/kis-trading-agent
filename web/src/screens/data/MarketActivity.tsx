import { useState } from "react";
import { Chip } from "../../components/Chip";
import { Table, type Column } from "../../components/Table";
import { fmtDate, fmtInt, fmtMoney, fmtPct, fmtTime } from "../../lib/format";
import { useNow, useResource } from "../../lib/hooks";
import { body } from "../performance/ResourceView";
import { useMarket } from "./Data.tsx";

type Ranking = "turnover" | "gain" | "loss";
type ActivityRow = {
  ticker: string; name: string | null; market: string; exchange: string;
  currency: "KRW" | "USD"; source_rank: number | null;
  price: string | null; change_pct: number | null; volume: number | null; turnover: string | null;
  received_at: string | null; stale: boolean;
  growth: { status: string; value: number | null; reason: string | null };
};
type Activity = {
  source: string; market: string; currency: string; received_at: string | null;
  source_asof: string | null; basis: string; status: string; stale: boolean;
  coverage: { expected: string[]; successful: string[]; failed: string[]; complete: boolean; session: string };
  rankings: Record<Ranking, ActivityRow[]>;
};
const LABELS: Record<Ranking, string> = { turnover: "거래대금", gain: "상승률", loss: "하락률" };
const MAX_AGE_MS = 120_000;
function isExpired(receivedAt: string | null, now: number): boolean {
  const age = receivedAt ? now - Date.parse(receivedAt) : NaN;
  return !Number.isFinite(age) || age < 0 || age > MAX_AGE_MS;
}
const columns: Column<ActivityRow>[] = [
  { key: "ticker", label: "종목", width: "20%", render: (r) => <span>{r.ticker}{r.name && r.name !== r.ticker ? ` ${r.name}` : ""}</span> },
  { key: "exchange", label: "거래소 / 원천 순위", width: "16%", render: (r) => `${r.exchange} / ${r.source_rank ?? "—"}` },
  { key: "price", label: "가격", width: "15%", align: "num", render: (r) => `${fmtMoney(r.price, r.currency)} ${r.currency}` },
  { key: "change_pct", label: "가격 등락률", width: "12%", align: "num", render: (r) => fmtPct(r.change_pct) },
  { key: "turnover", label: "누적 거래대금", width: "19%", align: "num", render: (r) => `${fmtMoney(r.turnover, r.currency)} ${r.currency}` },
  { key: "growth", label: "5분 거래대금 증감률", width: "18%", align: "num", render: (r) => !r.stale && r.growth.status === "available" && r.growth.value !== null ? fmtPct(r.growth.value, 1) : <span className="data-muted" title="같은 거래일·거래 세션의 유효한 관측값이 필요합니다">계산 불가</span> },
];

export function MarketActivity() {
  const [market] = useMarket();
  const [ranking, setRanking] = useState<Ranking>("turnover");
  const res = useResource<Activity>("/quotes/activity", { market }, ["runs"]);
  const now = useNow().getTime();
  return (
    <div className="market-activity">
      <h3 className="perf-sub">거래대금·등락 순위</h3>
      <div className="perf-filters">
        <div className="segment" role="group" aria-label="순위 종류">
          {(Object.keys(LABELS) as Ranking[]).map((key) => <button type="button" key={key} className="segment-btn" aria-pressed={ranking === key} onClick={() => setRanking(key)}>{LABELS[key]}</button>)}
        </div>
        <button className="btn" type="button" disabled={res.loading} onClick={res.reload}>순위 새로고침</button>
      </div>
      {body(res, "거래대금 순위", (s) => {
        const rowStale = (r: ActivityRow) => r.stale || isExpired(r.received_at, now);
        const stale = s.stale || isExpired(s.received_at, now) || Object.values(s.rankings).some((rows) => rows.some(rowStale));
        const rows = s.rankings[ranking].map((r) => ({ ...r, stale: rowStale(r) }));
        return (
          <>
            <p className="perf-note">
              <strong>KIS {s.market} / {s.currency}</strong>{" "}
              <Chip tone="warn">{s.status === "unavailable" ? "수집 자료 없음" : "일부 범위"}</Chip>{" "}
              {stale ? <Chip tone="warn">갱신 지연</Chip> : null}
            </p>
            <p className="data-muted">수신 {s.received_at ? `${fmtDate(s.received_at)} ${fmtTime(s.received_at)} KST` : "기록 없음"} / 원천 기준 시각 {s.source_asof ? `${fmtDate(s.source_asof)} ${fmtTime(s.source_asof)} KST` : "미제공"} / 거래 세션 미확인</p>
            <p className="perf-note">거래소별 API가 제공한 상위 종목 범위입니다. 전체 시장 순위로 확정할 수 없습니다. 5분 증감률은 관측된 최근 5분 거래대금을 직전 5분과 비교합니다.</p>
            <Table columns={columns} rows={rows} rowKey={(r) => `${r.exchange}:${r.ticker}`} empty="수집 자료 없음 — 데몬의 시세 수집 상태를 확인하세요" />
            <p className="data-muted">조회 성공 {fmtInt(s.coverage.successful.length)}개 / 실패 {fmtInt(s.coverage.failed.length)}개{ s.coverage.failed.length ? ` (${s.coverage.failed.join(", ")})` : ""}</p>
          </>
        );
      })}
    </div>
  );
}
