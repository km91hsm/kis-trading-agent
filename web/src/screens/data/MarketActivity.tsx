import { Link, useSearchParams } from "react-router-dom";
import { Chip } from "../../components/Chip";
import { Pager } from "../../components/Pager";
import { Table, type Column } from "../../components/Table";
import { fmtDate, fmtInt, fmtMoney, fmtPct, fmtTime } from "../../lib/format";
import { useNow, useResource } from "../../lib/hooks";
import { usePaged } from "../../lib/usePaged";
import { body } from "../performance/ResourceView";
import { useMarket } from "./Data.tsx";

type Ranking = "turnover" | "gain" | "loss";
type ActivityRow = {
  ticker: string; name: string | null; market: string; exchange: string;
  currency: "KRW" | "USD"; source_rank: number | null;
  price: string | null; change_pct: number | null; volume: number | null; turnover: string | null;
  turnover_unit?: string;
  received_at: string | null; stale: boolean;
  growth: { status: string; value: number | null; reason: string | null };
};
type Activity = {
  source: string; market: string; currency: string; received_at: string | null;
  source_asof: string | null; basis: string; status: string; stale: boolean;
  poll_window?: "open" | "closed"; collection_state?: string;
  coverage: { expected: string[]; successful: string[]; failed: string[]; pending?: string[]; complete: boolean; session: string };
  rankings: Record<Ranking, ActivityRow[]>;
};
const LABELS: Record<Ranking, string> = { turnover: "거래대금", gain: "상승률", loss: "하락률" };
const MAX_AGE_MS = 120_000;
const GROWTH_REASONS: Record<string, string> = {
  stale_observation: "관측값 갱신 지연", observation_session_unknown: "같은 거래 세션인지 확인 필요",
  three_boundaries_not_ready: "10분 관측 이력 대기", insufficient_history: "관측 이력 부족",
  missing_counter: "거래대금 관측값 누락", counter_reset_or_negative_delta: "누적 거래대금 초기화 또는 감소",
  zero_denominator_or_negative_delta: "비교 구간 거래대금이 0이거나 감소", nonfinite_growth: "유효한 증가율 없음",
};
function growthValue(r: ActivityRow): number | null {
  return !r.stale && r.growth.status === "available" && typeof r.growth.value === "number" && Number.isFinite(r.growth.value) ? r.growth.value : null;
}
function isExpired(receivedAt: string | null, now: number): boolean {
  const age = receivedAt ? now - Date.parse(receivedAt) : NaN;
  return !Number.isFinite(age) || age < 0 || age > MAX_AGE_MS;
}
function chartHref(r: ActivityRow, params: URLSearchParams) {
  const next = new URLSearchParams(params);
  next.set("market", r.market); next.set("ticker", r.ticker); next.set("exchange", r.exchange);
  return `/data?${next}#stock-chart`;
}
const columns = (params: URLSearchParams): Column<ActivityRow>[] => [
  { key: "ticker", label: "종목 · 차트", width: "20%", long: true, render: (r) => <Link data-activity-ticker to={chartHref(r, params)}>{r.ticker}{r.name && r.name !== r.ticker ? ` ${r.name}` : ""}</Link> },
  { key: "exchange", label: "거래소 / 원천 순위", width: "16%", render: (r) => `${r.exchange} / ${r.source_rank ?? "—"}` },
  { key: "price", label: "가격", width: "15%", align: "num", render: (r) => `${fmtMoney(r.price, r.currency)} ${r.currency}` },
  { key: "change_pct", label: "가격 등락률", width: "12%", align: "num", render: (r) => fmtPct(r.change_pct) },
  { key: "turnover", label: "누적 거래대금", width: "19%", align: "num", render: (r) => r.turnover === null ? "—" : r.turnover_unit === r.currency ? `${fmtMoney(r.turnover, r.currency)} ${r.currency}` : <span className="activity-turnover"><span>{r.turnover}</span>{" "}<span className="data-muted">(원천값 · 단위 미확인)</span></span> },
  { key: "growth", label: "5분 거래대금 증감률", width: "18%", render: (r) => growthValue(r) !== null ? <span className="num">{fmtPct(r.growth.value, 1)}</span> : <span className="activity-unavailable"><span>계산 불가</span><small className="data-muted">{r.stale ? "관측값 갱신 지연" : GROWTH_REASONS[r.growth.reason ?? ""] ?? "같은 거래 세션의 유효 관측값 필요"}</small></span> },
];

function ActivityTable({ rows, params, empty }: { rows: ActivityRow[]; params: URLSearchParams; empty: string }) {
  const paged = usePaged(rows, 30, "apage");
  return <><Table columns={columns(params)} rows={paged.rows} rowKey={(r) => `${r.exchange}:${r.ticker}`} empty={empty} /><Pager {...paged} /></>;
}

export function MarketActivity() {
  const [market] = useMarket();
  const [params, setParams] = useSearchParams();
  const requested = params.get("ranking");
  const ranking: Ranking = requested === "gain" || requested === "loss" ? requested : "turnover";
  const sort = params.get("sort") === "growth" ? "growth" : "source";
  const filter = params.get("activity_q") ?? "";
  const growthOnly = params.get("growth_only") === "1";
  const change = (key: string, value: string) => setParams((prev) => {
    const next = new URLSearchParams(prev);
    if (value) next.set(key, value); else next.delete(key);
    next.delete("apage");
    return next;
  }, { replace: true });
  const res = useResource<Activity>("/quotes/activity", { market }, ["runs"]);
  const now = useNow().getTime();
  return (
    <div className="market-activity">
      <h3 className="perf-sub">거래대금·등락 순위</h3>
      <div className="perf-filters">
        <div className="segment" role="group" aria-label="순위 종류">
          {(Object.keys(LABELS) as Ranking[]).map((key) => <button type="button" key={key} className="segment-btn" aria-pressed={ranking === key} onClick={() => change("ranking", key === "turnover" ? "" : key)}>{LABELS[key]}</button>)}
        </div>
        <button className="btn" type="button" disabled={res.loading} onClick={res.reload}>순위 새로고침</button>
      </div>
      <div className="activity-filters">
        <label>종목 필터<input aria-label="순위 종목 필터" name="activity_q" autoComplete="off" spellCheck={false} value={filter} placeholder="예: 삼성 또는 AAPL…" onChange={(e) => change("activity_q", e.target.value)} /></label>
        <label>정렬<select aria-label="순위 정렬" value={sort} onChange={(e) => change("sort", e.target.value === "source" ? "" : "growth")}><option value="source">거래대금·등락 순</option><option value="growth">5분 거래대금 증가율 순</option></select></label>
        <label className="activity-check"><input type="checkbox" checked={growthOnly} onChange={(e) => change("growth_only", e.target.checked ? "1" : "")} />거래대금 증가 종목만</label>
      </div>
      {body(res, "거래대금 순위", (s) => {
        const rowStale = (r: ActivityRow) => r.stale || isExpired(r.received_at, now);
        const stale = s.stale || isExpired(s.received_at, now) || Object.values(s.rankings).some((rows) => rows.some(rowStale));
        const rows = s.rankings[ranking].map((r) => ({ ...r, stale: rowStale(r) }))
          .filter((r) => `${r.ticker} ${r.name ?? ""}`.toLocaleLowerCase().includes(filter.trim().toLocaleLowerCase()))
          .filter((r) => !growthOnly || (growthValue(r) ?? -Infinity) > 0);
        if (sort === "growth") rows.sort((a, b) => (growthValue(b) ?? -Infinity) - (growthValue(a) ?? -Infinity));
        const waiting = s.collection_state === "waiting_for_open" || s.collection_state === "awaiting_first";
        const closed = s.poll_window === "closed";
        const empty = s.collection_state === "empty";
        const disabled = s.collection_state === "disabled";
        const failures = s.coverage.failed.length;
        const status = disabled ? "거래대금 수집 꺼짐" : s.collection_state === "waiting_for_open" ? "장 시작 후 첫 수집 대기"
          : s.collection_state === "awaiting_first" ? "첫 관측 대기"
          : empty ? "조회 완료 · 결과 0건"
          : s.status === "unavailable" ? "수집 자료 없음" : "일부 범위";
        return (
          <>
            <p className="perf-note">
              <strong>KIS {s.market} / {s.currency}</strong>{" "}
              <Chip tone={disabled || waiting || empty || closed && !failures ? "muted" : "warn"}>{status}</Chip>{" "}
              {closed && !waiting ? <Chip>정규장 관측 시간 밖</Chip> : null}{" "}
              {failures ? <Chip tone="fail">최근 조회 오류</Chip> : null}{" "}
              {stale && !waiting && !closed && !disabled ? <Chip tone="warn">갱신 지연</Chip> : null}
            </p>
            <p className="data-muted">수신 {s.received_at ? `${fmtDate(s.received_at)} ${fmtTime(s.received_at)} KST` : "기록 없음"} / 원천 기준 시각 {s.source_asof ? `${fmtDate(s.source_asof)} ${fmtTime(s.source_asof)} KST` : "미제공"} / 거래 세션 미확인</p>
            <p className="perf-note">수집된 상위 종목 안에서 비교합니다. 전체 시장 순위는 아닙니다.</p>
            <ActivityTable params={params} rows={rows} empty={waiting ? `${status} — 정규장 관측 시간에 자동 수집합니다` : filter || growthOnly ? "조건에 맞는 관측 종목이 없습니다" : empty ? "조회한 범위에 종목이 없습니다" : "수집 자료 없음 — 데이터 수집 상태를 확인하세요"} />
            <p className="data-muted">유효 이력 {fmtInt(s.coverage.successful.length)}개 / 최근 실패 {fmtInt(failures)}개 / 미수집 {fmtInt(s.coverage.pending?.length ?? 0)}개</p>
            <details className="activity-notes"><summary>비교 기준과 수집 범위</summary><p>5분 증감률은 최근 5분 거래대금을 직전 5분과 비교합니다. 최소 10분의 같은 거래 세션 관측이 필요합니다. 정규장 시간 기준이며 임시 휴장 여부와 원천 거래 세션은 별도 확인이 필요합니다.</p>{failures ? <p>조회 오류 범위: {s.coverage.failed.join(", ")}</p> : null}<Link to="/system/jobs#runs">거래대금 수집 실행 기록 보기</Link></details>
          </>
        );
      })}
    </div>
  );
}
