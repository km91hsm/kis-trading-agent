// 그날 결정 목록 — 레인 합본(`GET /decisions`), 시각 역순, 상세 링크(단타는 없음), CSV.
// 종목 칸은 코드+이름만, 내용 칸은 최종 의견 요약(summary) — 판단(HOLD/BUY)은 방향 열에만.
import { Link, useSearchParams } from "react-router-dom";
import { Chip } from "../../components/Chip";
import { Table, type Column } from "../../components/Table";
import { Pager } from "../../components/Pager";
import { usePaged } from "../../lib/usePaged";
import { csvUrl } from "../../lib/api";
import { fmtTime } from "../../lib/format";
import { decisionRows, LANE_LABEL, type Decision, type DecisionRow, type Lane } from "./today.ts";

const COLUMNS: Column<DecisionRow>[] = [
  { key: "at", label: "시각", width: "88px", render: (r) => fmtTime(r.at) },
  { key: "lane", label: "매매 유형", width: "110px", align: "chip", render: (r) => <Chip>{r.kind === "일반 종목" ? LANE_LABEL[r.lane as Lane] ?? r.lane : r.kind}</Chip> },
  { key: "ticker", label: "종목", width: "200px", render: (r) => (r.href ? <Link to={r.href}>{r.ticker}</Link> : r.ticker) },
  {
    key: "summary",
    label: "내용",
    width: "auto",
    long: true,
    render: (r) => r.summary ?? "—",
  },
  { key: "side", label: "방향", width: "64px" },
  { key: "qty", label: "수량", width: "72px", align: "num", render: (r) => (r.qty ?? "—").toString() },
  { key: "account_id", label: "계좌", width: "112px", render: (r) => r.account_id ?? "—" },
  { key: "status", label: "상태", width: "72px", align: "chip", render: (r) => <span title={r.raw ?? undefined}>{r.status}</span> },
];

export function DecisionsList({ items, partial, date }: { items: Decision[]; partial: boolean; date: string }) {
  const [params, setParams] = useSearchParams();
  const market = params.get("decision_market") ?? "";
  const kind = params.get("decision_kind") ?? "";
  const search = params.get("decision_search") ?? "";
  const filtered = items.filter((d) => (!market || d.market === market) &&
    (!kind || (kind === "hedge" ? d.source === "hedge" : kind === "hold" ? (d.action === "HOLD" || d.intent === "HOLD") : d.lane === "spot" && d.source !== "hedge" && d.source !== "cash_park")) &&
    (!search || [d.ticker, d.name, d.summary].join(" ").toLocaleLowerCase().includes(search.toLocaleLowerCase())));
  const paged = usePaged(decisionRows(filtered), 30, "decision_page");
  const change = (key: string, value: string) => setParams((prev) => {
    const next = new URLSearchParams(prev);
    if (value) next.set(key, value); else next.delete(key);
    next.delete("decision_page");
    return next;
  }, { replace: true });
  return (
    <>
    <div className="decision-filters">
      <label>시장<select aria-label="판단 시장" value={market} onChange={(e) => change("decision_market", e.target.value)}>
        <option value="">전체 시장</option>{Array.from(new Set(items.map((d) => d.market).filter(Boolean))).map((m) => <option key={m} value={m!}>{m}</option>)}
      </select></label>
      <label>종류<select aria-label="판단 종류" value={kind} onChange={(e) => change("decision_kind", e.target.value)}>
        <option value="">전체 판단</option><option value="stock">일반 현물 종목</option><option value="hedge">방어용 헤지</option><option value="hold">보류 판단</option>
      </select></label>
      <label>종목·사유 검색<input name="decision-search" type="search" aria-label="판단 검색" value={search} onChange={(e) => change("decision_search", e.target.value)} placeholder="종목명·코드·사유…" autoComplete="off" /></label>
      <span className="data-muted" role="status">{filtered.length}건 / 전체 {items.length}건</span>
    </div>
    <Table
      columns={COLUMNS}
      rows={paged.rows}
      rowKey={(r) => r.key}
      partial={partial}
      csvHref={items.length ? csvUrl("/decisions", { date }) : undefined}
      empty={items.length ? "조건에 맞는 판단이 없습니다. 필터나 검색어를 바꿔보세요." : "이 날 결정이 없습니다"}
    />
    {market || kind || search ? <p className="data-muted">CSV에는 선택한 날짜의 전체 판단이 포함됩니다.</p> : null}
    <Pager {...paged} />
    </>
  );
}
