// 「결정 상세」 화면 — `/decisions/:asset/:id`. 돌아가기·머리(코드+이름)·근거 전문(산문)·라운드 표(spot)·에이전트 보고(산문, 신호 칩)·
// 토론 턴 details 안 표(spot)·체크포인트·리스크 체크 표(spot 게이트 / fo 체크)·이어진 주문·체결 표(spot)·복기 메모리 연결(spot).
// 계좌별 「결과」 표는 게이트·주문 표와 같은 내용이라 두지 않는다(/outcomes 는 부르지 않음).
// 섹션은 데이터 전에 바로 그리고, fo 응답에 없는 절(라운드·토론·체크포인트 JSON·주문)은 그리지 않는다.
import { useEffect, useState } from "react";
import { Link, useLocation, useNavigate, useParams } from "react-router-dom";
import { Card } from "../../components/Card";
import { Chip } from "../../components/Chip";
import { Empty } from "../../components/Empty";
import { Table, type Column } from "../../components/Table";
import { ApiError, getJson } from "../../lib/api";
import { fmtInt } from "../../lib/format";
import { GLOSSARY, sideLabel, termLabel } from "../../lib/glossary";
import { useResource, type Resource } from "../../lib/hooks";
import { normalizeTrace, SIGNAL, type FoCheck, type TraceDebate, type TraceGate, type TraceOrder, type TraceResp } from "../../lib/trace";
import { actionLabel, decisionHeader, roundRows, timingLabel, type FoDetail, type RoundRow, type SpotDetail } from "./decision.ts";

function BackLink() {
  const navigate = useNavigate();
  const loc = useLocation();
  return (
    <Link
      to="/"
      className="back-link"
      onClick={(e) => {
        if (loc.key !== "default") {
          e.preventDefault();
          navigate(-1);
        }
      }}
    >
      ‹ 돌아가기
    </Link>
  );
}

/** 아직 없는 자원의 자리: 오류면 문구, 아니면 「불러오는 중」. */
function Pending({ res, what }: { res: Resource<unknown>; what: string }) {
  return res.error ? <Empty title={`${what}을(를) 불러오지 못했습니다`}>{res.error.message}</Empty> : <Empty title="불러오는 중" />;
}

const pct = (v: number | null | undefined) => (typeof v === "number" ? `${Math.round(v * 100)}%` : "—");
const statusLabel = (s: string | null | undefined) => (s ? (GLOSSARY[`status.${s}`]?.label ?? s) : "—");
const STATUS_TONE: Record<string, "ok" | "warn" | "fail" | "muted"> = { filled: "ok", accepted: "ok", failed: "fail", rejected: "fail", cancelled: "muted", pending: "warn" };
const statusChip = (s: string | null | undefined) => (s ? <Chip tone={STATUS_TONE[s] ?? "muted"}>{statusLabel(s)}</Chip> : "—");
const gateChip = (approved: boolean | null | undefined) =>
  approved === true ? <Chip tone="ok">{termLabel("gate.pass")}</Chip> : approved === false ? <Chip tone="fail">{termLabel("gate.reject")}</Chip> : "—";
const signalChip = (s: string | null | undefined) => <Chip tone={s === "BULLISH" ? "ok" : s === "BEARISH" ? "fail" : "muted"}>{SIGNAL[s ?? ""] ?? s ?? "—"}</Chip>;

const ROUND_COLUMNS: Column<RoundRow>[] = [
  { key: "round", label: "라운드", width: "14%", render: (r) => `r${r.round}` },
  { key: "action", label: "판단", width: "18%", align: "chip", render: (r) => <Chip>{actionLabel(r.action)}</Chip> },
  { key: "stop_loss", label: "손절", width: "20%", align: "num", render: (r) => (r.stop_loss === "—" ? "—" : fmtInt(r.stop_loss)) },
  { key: "source", label: "출처", width: "24%", render: (r) => r.source },
  { key: "at", label: "시각", width: "24%", render: (r) => r.at },
];
type DebateRow = TraceDebate & { key: number };
const DEBATE_COLUMNS: Column<DebateRow>[] = [
  { key: "round", label: "라운드", width: "14%", render: (r) => `r${r.round ?? 0}` },
  { key: "side", label: "측", width: "18%", align: "chip", render: (r) => <Chip tone={r.side === "bull" ? "ok" : "fail"}>{r.side === "bull" ? "강세 측" : "약세 측"}</Chip> },
  { key: "stance", label: "판단", width: "20%", align: "chip", render: (r) => signalChip(r.stance) },
  { key: "conviction", label: "확신", width: "20%", align: "num", render: (r) => pct(r.conviction) },
  { key: "timing", label: "타이밍", width: "28%", render: (r) => timingLabel(r.timing) },
];
type GateRow = TraceGate & { key: number };
const GATE_COLUMNS: Column<GateRow>[] = [
  { key: "account_id", label: "계좌", width: "18%" },
  { key: "approved", label: "결과", width: "12%", align: "chip", render: (r) => gateChip(r.approved) },
  { key: "qty", label: "수량", width: "12%", align: "num", render: (r) => (r.qty === null || r.qty === undefined ? "—" : `${fmtInt(r.qty)}주`) },
  { key: "reasons", label: "사유", width: "auto", long: true, render: (r) => (r.reasons ?? []).join("; ") || "—" },
  {
    key: "note",
    label: "비고",
    width: "20%",
    align: "chip",
    render: (r) => (
      <span className="chips">
        {r.dry_run ? <Chip tone="warn">{termLabel("dry_run")}</Chip> : null}
        {r.note ? <span className="data-muted">{r.note}</span> : null}
        {!r.dry_run && !r.note ? "—" : null}
      </span>
    ),
  },
];
type CheckRow = FoCheck & { stage: string };
const CHECK_COLUMNS: Column<CheckRow>[] = [
  { key: "stage", label: "단계", width: "28%", long: true, render: (r) => r.stage },
  { key: "approved", label: "결과", width: "14%", align: "chip", render: (r) => (r.approved === true || r.approved === false ? gateChip(r.approved) : r.status ? statusChip(r.status) : "—") },
  { key: "reasons", label: "사유", width: "auto", render: (r) => [(r.reasons ?? []).join("; "), r.note ? `(${r.note})` : ""].filter(Boolean).join(" ") || "—" },
  { key: "error", label: "오류", width: "22%", render: (r) => r.error ?? "—" },
];
type OrderRow = TraceOrder & { key: number };
const ORDER_COLUMNS: Column<OrderRow>[] = [
  { key: "account_id", label: "계좌", width: "16%" },
  { key: "side", label: "방향", width: "10%", align: "chip", render: (r) => (r.side ? <Chip tone={r.side.toUpperCase() === "BUY" ? "ok" : "fail"}>{sideLabel(r.side)}</Chip> : "—") },
  { key: "qty", label: "수량", width: "10%", align: "num", render: (r) => (r.qty === null || r.qty === undefined ? "—" : `${fmtInt(r.qty)}주`) },
  { key: "price", label: "가격", width: "12%", align: "num", render: (r) => (r.price ? fmtInt(r.price) : "시장가") },
  { key: "status", label: "상태", width: "12%", align: "chip", render: (r) => statusChip(r.status) },
  { key: "order_no", label: "주문번호", width: "14%", render: (r) => (r.order_no ? <code>{r.order_no}</code> : "—") },
  { key: "message", label: "메시지", width: "auto", long: true, render: (r) => r.message ?? "—" },
];

function Checkpoints({ asset, id, stages }: { asset: string; id: string; stages: string[] }) {
  const [stage, setStage] = useState<string | null>(null);
  const [cp, setCp] = useState<{ data?: unknown; error?: string } | null>(null);
  useEffect(() => {
    if (!stage) return;
    const ctl = new AbortController();
    setCp(null);
    getJson<{ data: unknown }>(`/decisions/spot/${id}/checkpoints/${encodeURIComponent(stage)}`, undefined, { signal: ctl.signal }).then(
      (r) => setCp({ data: r.data }),
      (e: unknown) => {
        if ((e as { name?: string })?.name === "AbortError") return;
        setCp({ error: e instanceof ApiError ? e.message : String(e) });
      },
    );
    return () => ctl.abort();
  }, [id, stage]);
  if (!stages.length) return <Empty title="체크포인트 없음" />;
  if (asset !== "spot") return <ul className="stage-chips">{stages.map((s) => <li key={s}><Chip>{s}</Chip></li>)}</ul>;
  return (
    <>
      <ul className="stage-chips">
        {stages.map((s) => (
          <li key={s}>
            <button type="button" className="stage-chip" aria-pressed={stage === s} onClick={() => setStage(stage === s ? null : s)}>
              {s}
            </button>
          </li>
        ))}
      </ul>
      {stage ? (
        cp === null ? (
          <Empty title="불러오는 중" />
        ) : cp.error ? (
          <Empty title="체크포인트를 불러오지 못했습니다">{cp.error}</Empty>
        ) : (
          <pre className="checkpoint-json">{JSON.stringify(cp.data, null, 2)}</pre>
        )
      ) : null}
    </>
  );
}

export function Decision() {
  const { asset = "spot", id = "" } = useParams();
  const spot = asset === "spot";
  const detail = useResource<SpotDetail | FoDetail>(`/decisions/${asset}/${id}`, undefined, ["decisions"]);
  const rawTrace = useResource<unknown>(`/decisions/${asset}/${id}/trace`, undefined, ["decisions", "orders"]);

  const notFound = [detail.error, rawTrace.error].find((e) => e?.status === 404);
  if (notFound) {
    return (
      <div className="screen" data-screen="decision">
        <p className="back-line"><BackLink /></p>
        <Empty title="결정 없음">{notFound.message}</Empty>
      </div>
    );
  }

  const d = detail.data;
  const head = d ? decisionHeader(d.decision) : null;
  const t: TraceResp | null = rawTrace.data ? normalizeTrace(spot ? "spot" : "fo", rawTrace.data) : null;
  const spotDetail = spot && d ? (d as SpotDetail) : null;
  const ticker = spotDetail?.decision.ticker;

  return (
    <div className="screen" data-screen="decision">
      <p className="back-line"><BackLink /></p>
      <h1 className="screen-title">결정 상세</h1>

      <section id="header" className="screen-section">
        {head ? (
          <Card title={head.title}>
            <div className="decision-chips">{head.chips.map((c) => <Chip key={c}>{c}</Chip>)}</div>
          </Card>
        ) : (
          <Pending res={detail} what="결정" />
        )}
      </section>

      <section id="rationale" className="screen-section">
        <h2>근거 전문</h2>
        {head ? (
          <>
            {head.summaryLines.length ? <ul className="decision-lines">{head.summaryLines.map((l, i) => <li key={i}>{l}</li>)}</ul> : <Empty title="근거 없음" />}
            {spotDetail?.summary ? <p className="decision-summary">{spotDetail.summary}</p> : null}
          </>
        ) : (
          <Pending res={detail} what="결정" />
        )}
      </section>

      {spot ? (
        <section id="rounds" className="screen-section">
          <h2>라운드</h2>
          {spotDetail ? (
            <Table columns={ROUND_COLUMNS} rows={roundRows(spotDetail.rounds)} rowKey={(r) => r.round} empty="재평가 라운드 없음" />
          ) : (
            <Pending res={detail} what="결정" />
          )}
        </section>
      ) : null}

      <section id="analysts" className="screen-section" hidden={!!t && !t.analysts.length}>
        <h2>에이전트 보고</h2>
        {t ? (
          <ul className="decision-lines">
            {t.analysts.map((a) => (
              <li key={a.role}>
                <strong>{a.label}</strong> {signalChip(a.signal)} <span className="data-muted">확신 {pct(a.confidence)}</span>
                {a.summary ? <p className="analyst-summary">{a.summary}</p> : null}
                {a.evidence.length ? <ul className="analyst-evidence">{a.evidence.map((e, i) => <li key={i}>{e}</li>)}</ul> : null}
              </li>
            ))}
          </ul>
        ) : (
          <Pending res={rawTrace} what="추적" />
        )}
      </section>

      {spot ? (
        <section id="debate" className="screen-section" hidden={!!t && !t.debate.length}>
          <h2>토론 턴</h2>
          {t ? (
            <details className="debate">
              <summary>토론 턴 {t.debate.length}개</summary>
              <Table columns={DEBATE_COLUMNS} rows={t.debate.map((x, i) => ({ ...x, key: i }))} rowKey={(r) => r.key} />
            </details>
          ) : (
            <Pending res={rawTrace} what="추적" />
          )}
        </section>
      ) : null}

      <section id="checkpoints" className="screen-section">
        <h2>체크포인트</h2>
        {t ? <Checkpoints asset={asset} id={id} stages={t.stages} /> : <Pending res={rawTrace} what="추적" />}
      </section>

      <section id={spot ? "gate" : "checks"} className="screen-section">
        <h2>리스크 체크</h2>
        {t ? (
          spot ? (
            <Table columns={GATE_COLUMNS} rows={t.gate.map((g, i) => ({ ...g, key: i }))} rowKey={(r) => r.key} empty="리스크 체크 없음" />
          ) : (
            <Table columns={CHECK_COLUMNS} rows={Object.entries(t.checks ?? {}).map(([stage, c]) => ({ ...c, stage }))} rowKey={(r) => r.stage} empty="리스크 체크 없음" />
          )
        ) : (
          <Pending res={rawTrace} what="추적" />
        )}
      </section>

      {spot ? (
        <section id="orders" className="screen-section">
          <h2>이어진 주문·체결</h2>
          {t ? <Table columns={ORDER_COLUMNS} rows={t.orders.map((o, i) => ({ ...o, key: i }))} rowKey={(r) => r.key} empty="주문 없음" /> : <Pending res={rawTrace} what="추적" />}
        </section>
      ) : null}

      {spot ? (
        <section id="memory" className="screen-section">
          <h2>복기 메모리</h2>
          {ticker ? (
            <Link to={`/performance/memory?ticker=${encodeURIComponent(ticker)}`}>{ticker} 복기 메모리 보기</Link>
          ) : (
            <Pending res={detail} what="결정" />
          )}
        </section>
      ) : null}
    </div>
  );
}
