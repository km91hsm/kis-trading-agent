// 「결정 상세」 화면 — `/decisions/:asset/:id`. 돌아가기·머리·근거 전문·라운드(spot)·에이전트 보고·토론 턴(spot)·
// 체크포인트·리스크 체크·이어진 주문·체결·결과(spot)·복기 메모리 연결(spot).
// 섹션은 데이터 전에 바로 그리고, fo 응답에 없는 절(라운드·토론·체크포인트 JSON·주문·결과)은 그리지 않는다.
import { useEffect, useState } from "react";
import { Link, useLocation, useNavigate, useParams } from "react-router-dom";
import { Card } from "../../components/Card";
import { Chip } from "../../components/Chip";
import { Empty } from "../../components/Empty";
import { Table, type Column } from "../../components/Table";
import { ApiError, getJson } from "../../lib/api";
import { fmtTime } from "../../lib/format";
import { GLOSSARY, termLabel } from "../../lib/glossary";
import { useResource, type Resource } from "../../lib/hooks";
import { checkLine, gateLine, normalizeTrace, orderLine, SIGNAL, type TraceResp } from "../../lib/trace";
import { actionLabel, decisionHeader, roundRows, type FoDetail, type Outcome, type OutcomesResp, type SpotDetail } from "./decision.ts";

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

type OutcomeRow = Outcome & { key: string };
const OUTCOME_COLUMNS: Column<OutcomeRow>[] = [
  { key: "account_id", label: "계좌", width: "112px" },
  { key: "approved", label: "게이트", width: "72px", align: "chip", render: (r) => (r.approved ? termLabel("gate.pass") : termLabel("gate.reject")) },
  { key: "adjusted_qty", label: "수량", width: "72px", align: "num", render: (r) => String(r.order_qty ?? r.adjusted_qty ?? "—") },
  { key: "order_price", label: "가격", width: "96px", align: "num", render: (r) => r.order_price ?? "시장가" },
  { key: "order_status", label: "주문", width: "72px", align: "chip", render: (r) => statusLabel(r.order_status) },
  { key: "reasons", label: "사유", width: "auto", long: true, render: (r) => (r.reasons ?? []).join("; ") || "—" },
  { key: "checked_at", label: "시각", width: "88px", render: (r) => fmtTime(r.checked_at) },
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
  const outcomes = useResource<OutcomesResp>(`/decisions/${asset}/${id}/outcomes`, undefined, ["orders", "decisions"], { enabled: spot });

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
            spotDetail.rounds.length ? (
              <ol className="decision-lines">
                {roundRows(spotDetail.rounds).map((r) => (
                  <li key={r.round} className="round-row">
                    r{r.round} · {actionLabel(r.action)} · 손절 {r.stop_loss} · {r.source} · {r.at}
                  </li>
                ))}
              </ol>
            ) : (
              <Empty title="재평가 라운드 없음" />
            )
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
                <strong>{a.label}</strong> {SIGNAL[a.signal ?? ""] ?? a.signal ?? "—"} · 확신 {pct(a.confidence)}
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
              <ol className="decision-lines">
                {t.debate.map((x, i) => (
                  <li key={i} className="debate-turn">
                    r{x.round ?? 0} · {x.side === "bull" ? "강세 측" : "약세 측"} · {SIGNAL[x.stance ?? ""] ?? x.stance ?? "—"} · 확신 {pct(x.conviction)}
                    {x.timing ? ` · ${x.timing}` : ""}
                  </li>
                ))}
              </ol>
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
            t.gate.length ? <ul className="decision-lines">{t.gate.map((g, i) => <li key={i}>{gateLine(g)}</li>)}</ul> : <Empty title="리스크 체크 없음" />
          ) : Object.keys(t.checks ?? {}).length ? (
            <ul className="decision-lines">{Object.entries(t.checks ?? {}).map(([s, c]) => <li key={s}>{checkLine(s, c)}</li>)}</ul>
          ) : (
            <Empty title="리스크 체크 없음" />
          )
        ) : (
          <Pending res={rawTrace} what="추적" />
        )}
      </section>

      {spot ? (
        <section id="orders" className="screen-section">
          <h2>이어진 주문·체결</h2>
          {t ? (
            t.orders.length ? <ul className="decision-lines">{t.orders.map((o, i) => <li key={i}>{orderLine(o)}</li>)}</ul> : <Empty title="주문 없음" />
          ) : (
            <Pending res={rawTrace} what="추적" />
          )}
          <div id="outcomes" className="decision-outcomes">
            <h3>결과</h3>
            {outcomes.data ? (
              <Table
                columns={OUTCOME_COLUMNS}
                rows={outcomes.data.outcomes.map((o, i) => ({ ...o, key: `${o.account_id}-${i}` }))}
                rowKey={(r) => r.key}
                empty="결과 없음"
              />
            ) : (
              <Pending res={outcomes} what="결과" />
            )}
          </div>
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
