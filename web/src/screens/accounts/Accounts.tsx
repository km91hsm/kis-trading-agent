// 「계좌·보유」 통합 화면 — 합계 띠(원화·달러·참고 환율), 전역 일시정지·재개, 계좌 표(행의 id 가 계좌별 화면 링크).
// 섹션 컨테이너(id = NAV 항목)는 데이터 전에 바로 그린다. `fo-positions` 는 파생 계좌 카드 묶음(Ruling P2) — 각 카드에서
// 계좌별 화면으로 간다. fo 계좌가 없을 때만 hidden. 전역 일시정지는 합계(/accounts/overview) 성공 여부와 무관하게 늘 그린다.
import { useState } from "react";
import { Link } from "react-router-dom";
import { Card } from "../../components/Card";
import { Chip } from "../../components/Chip";
import { Confirm } from "../../components/Confirm";
import { Empty } from "../../components/Empty";
import { Stats } from "../../components/Stats";
import { Table, type Column } from "../../components/Table";
import { errorText, patchJson } from "../../lib/api";
import { fmtDate, fmtPct } from "../../lib/format";
import { useResource } from "../../lib/hooks";
import {
  FO_STATE_LABEL,
  cardRows,
  moneyUnit,
  resumeNeedsConfirm,
  totals,
  type Account,
  type AccountCard,
  type FoStatus,
  type FoSummary,
  type Overview,
  type OverviewGroup,
} from "./accounts.ts";

/** 계좌 표 — 첫 열의 id 가 계좌별 화면 링크(data-account-row). 숫자 열은 오른쪽 정렬·tabular. */
const ACCOUNT_COLUMNS: Column<AccountCard>[] = [
  {
    key: "id", label: "계좌", width: "26%", long: true,
    render: (c) => (
      <>
        <Link to={c.href} data-account-row={c.id} className="account-link">{c.id}</Link>{" "}
        <Chip tone={c.live ? "live" : "muted"}>{c.envLabel}</Chip>
        {c.paused ? <> <Chip tone="fail">일시정지</Chip></> : null}
      </>
    ),
  },
  { key: "lane", label: "레인", width: "10%" },
  {
    key: "mode", label: "모드", width: "16%",
    render: (c) => (
      <>
        {c.mode}
        {c.paused ? <span className="account-mode-note"> (전체 일시정지로 신규 진입·판단 매도·승인 중지, 위험 청산 계속)</span> : null}
        {c.modeNote ? <span className="account-mode-note"> · {c.modeNote}</span> : null}
      </>
    ),
  },
  { key: "nav", label: "순자산", width: "16%", align: "num" },
  { key: "dayPnl", label: "당일 손익", width: "14%", align: "num" },
  { key: "positions", label: "보유", width: "8%", align: "num", render: (c) => `${c.positions}종목` },
  {
    key: "pending", label: "승인 대기", width: "10%", align: "chip",
    render: (c) => (c.pending > 0 ? <Chip tone="warn">{c.pending}건</Chip> : <span className="muted">없음</span>),
  },
];  // fmt: skip

export function Accounts() {
  const overview = useResource<Overview>("/accounts/overview", undefined, ["accounts", "positions", "control"]);
  const accounts = useResource<Account[]>("/accounts", undefined, ["accounts", "approvals", "control"]);
  // 전역 paused 는 /accounts 행마다 같은 값으로 실린다. 계좌가 없으면 합계 쪽 값, 둘 다 모르면 false(일시정지 버튼 — 안전한 방향).
  const paused = accounts.data?.length ? accounts.data.some((a) => a.paused) : (overview.data?.paused ?? false);
  const spotCards = accounts.data ? cardRows(accounts.data.filter((a) => a.lane !== "fo")) : [];
  const foAccounts = accounts.data ? accounts.data.filter((a) => a.lane === "fo") : null;
  // 파생 NAV·증거금은 /accounts 가 아니라 fo_nav_snapshot(/fo/summary) — 실패하면 카드는 /accounts 값으로.
  const foSummary = useResource<FoSummary>("/fo/summary", undefined, ["fo", "accounts"], { enabled: !!foAccounts?.length });
  const foStatus = new Map((foSummary.data?.accounts ?? []).map((a) => [a.id, a]));

  return (
    <div className="screen" data-screen="accounts">
      <h1 className="screen-title">계좌·보유</h1>
      <div className="screen-intro">
        <p className="screen-description">계좌별 보유·주문·위험을 확인합니다. 계좌를 선택하면 해당 계좌의 상세 기록으로 이동합니다.</p>
        <div className="screen-actions">
          <Link to="/system/features#regime">매수 정책 확인</Link>
          <Link to="/performance">계좌별 성과 비교</Link>
        </div>
      </div>

      <section id="overview" className="screen-section">
        <h2>계좌별 자산 현황</h2>
        {overview.error ? (
          <Empty title="합계를 불러오지 못했습니다">{overview.error.message}</Empty>
        ) : overview.data ? (
          <TotalsRow data={overview.data} accounts={accounts.data ?? null} />
        ) : (
          <Empty title="합계 불러오는 중" />
        )}
        <PauseControl
          paused={paused}
          confirmId={accounts.data ? resumeNeedsConfirm(accounts.data) : null}
          ready={!!accounts.data}
          onDone={() => {
            overview.reload();
            accounts.reload();
          }}
        />
        {accounts.error ? (
          <Empty title="계좌를 불러오지 못했습니다">{accounts.error.message}</Empty>
        ) : accounts.data ? (
          spotCards.length ? (
            <Table columns={ACCOUNT_COLUMNS} rows={spotCards} rowKey={(c) => c.id} empty="현물 계좌 없음" />
          ) : (
            <Empty title={accounts.data.length ? "현물 계좌 없음" : "계좌 없음"} />
          )
        ) : (
          <Empty title="계좌 불러오는 중" />
        )}
      </section>

      <section id="fo-positions" className="screen-section" hidden={foAccounts ? !foAccounts.length : !!accounts.error}>
        <h2>파생 계좌</h2>
        {foAccounts ? (
          <div className="account-grid">
            {foAccounts.map((a) => (
              <FoAccountCard key={a.id} account={a} status={foStatus.get(a.id)} />
            ))}
          </div>
        ) : (
          <Empty title="파생 계좌 불러오는 중" />
        )}
      </section>
    </div>
  );
}

const MODE_LABEL: Record<string, string> = { auto: "자동", approve: "승인", off: "끔" };

/** 파생 계좌 카드 — id·모의/실전·상태 칩(/fo/summary state)·모드(+메모)·NAV/증거금(/fo/summary, 없으면 /accounts 의 NAV·현금)·
 * 계좌별 화면 링크. 보유 상세는 계좌별 화면 「파생 보유」 절. */
function FoAccountCard({ account: a, status }: { account: Account; status?: FoStatus }) {
  const money = (v: Account["nav"] | undefined) => moneyUnit(v, a.currency);
  const live = a.env !== "paper";
  const items = status
    ? [
        { id: "nav", label: "NAV", value: money(status.nav), sub: status.nav_date ? `기준 ${fmtDate(status.nav_date)}` : undefined },
        {
          id: "margin", label: "증거금", value: money(status.margin_total),
          sub: status.margin_pct === null || status.margin_pct === undefined ? undefined : `NAV 의 ${fmtPct(status.margin_pct)}`,
        },
      ]
    : [
        { id: "nav", label: "NAV", value: money(a.nav) },
        { id: "cash", label: "현금", value: money(a.cash) },
      ];
  return (
    <Card
      title={a.id}
      right={
        <span className="chips">
          <Chip tone={live ? "live" : "muted"}>{live ? "실전" : "모의"}</Chip>
          {status ? <Chip tone={status.state === "active" ? "ok" : status.state === "halted" ? "fail" : "muted"}>{FO_STATE_LABEL[status.state] ?? status.state}</Chip> : null}
        </span>
      }
    >
      <p className="account-mode">
        모드 {MODE_LABEL[a.mode] ?? a.mode}
        {a.paused ? <span className="account-mode-note"> (전체 일시정지로 신규 진입·판단 매도·승인 중지, 위험 청산 계속)</span> : null}
        {a.mode_note ? <span className="account-mode-note"> · {a.mode_note}</span> : null}
      </p>
      <Stats cols={2} items={items} />
      <Link to={`/accounts/${encodeURIComponent(a.id)}`}>계좌별 화면 ›</Link>
    </Card>
  );
}

/** 합계 띠 — 한 묶음을 1px 선으로 세 칸(원화·달러·참고). 칸마다 범위(모의 n · 실전 n), 참고 칸은 환율·환산 합계(합계 계산에 쓰지 않음). */
function TotalsRow({ data, accounts }: { data: Overview; accounts: Account[] | null }) {
  let groups = Object.entries(data.by_env ?? {}) as [string, OverviewGroup][];
  if (!data.by_env) {
    const ids = [...new Set([...data.krw.accounts, ...data.usd.accounts])];
    const envs = new Set(ids.map((id) => accounts?.find((a) => a.id === id)?.env));
    if (envs.size > 1 || envs.has(undefined)) {
      return <Empty title="환경별 합계를 확인할 수 없습니다. 아래 계좌별 자산을 확인하세요." />;
    }
    groups = [[envs.values().next().value ?? "", data]];
  }
  return <div className="overview-groups">{groups.map(([env, group]) => (
    <div key={env} data-overview-env={env} className="overview-group">
      <h3>{env === "paper" ? "모의 계좌" : env === "real" ? "실전 계좌" : "계좌 합계"}</h3>
      <CurrencyTotals data={group} accounts={accounts} />
    </div>
  ))}</div>;
}

function CurrencyTotals({ data, accounts }: { data: OverviewGroup; accounts: Account[] | null }) {
  const t = totals(data, accounts);
  return (
    <div className="kpi-strip">
      <div className="kpi" data-kpi="krw">
        <span className="kpi-label">원화 합계 · {t.krwScope}</span>
        <span className="kpi-value num">{t.krw}</span>
        <span className="kpi-sub num">당일 손익 {t.krwPnl}</span>
      </div>
      <div className="kpi" data-kpi="usd">
        <span className="kpi-label">달러 합계 · {t.usdScope}</span>
        <span className="kpi-value num">{t.usd}</span>
        <span className="kpi-sub num">당일 손익 {t.usdPnl}</span>
      </div>
      <div className="kpi" data-kpi="fx">
        <span className="kpi-label">참고 · 합계 계산에 쓰지 않음</span>
        <span className="kpi-sub">{t.converted ? `${t.fxLine} → ${t.converted}` : t.fxLine}</span>
      </div>
    </div>
  );
}

/** 일시정지는 확인만, 재개는 실전 auto 계좌가 있으면 그 id 재입력 → `confirm`. 계좌 목록 전에는 버튼을 끈다(confirm 대상 판단 불가). */
function PauseControl({ paused, confirmId, ready, onDone }: { paused: boolean; confirmId: string | null; ready: boolean; onDone(): void }) {
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const label = paused ? "전체 자동매매 재개" : "전체 자동매매 일시정지";

  async function submit(confirm?: string) {
    setOpen(false);
    setBusy(true);
    setError(null);
    try {
      await patchJson("/settings/paused", paused ? (confirm ? { value: false, confirm } : { value: false }) : { value: true });
      onDone();
    } catch (e) {
      setError(errorText(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="pause-control">
      <button type="button" className={paused ? "btn btn-primary" : "btn"} disabled={busy || (paused && !ready)} onClick={() => setOpen(true)}>
        {label}
      </button>
      {error ? (
        <p className="pause-error" role="alert">
          {error}
        </p>
      ) : null}
      <Confirm
        open={open}
        title={paused ? "전체 재개" : "전체 일시정지"}
        body={paused ? "모든 계좌의 자동 매매를 다시 켭니다." : "모든 계좌의 신규 진입·판단 매도·승인 실행을 멈춥니다. 손절 등 위험 청산은 계속됩니다."}
        live={paused && confirmId ? { accountId: confirmId } : undefined}
        onConfirm={(confirm) => void submit(confirm)}
        onClose={() => setOpen(false)}
      />
    </div>
  );
}
