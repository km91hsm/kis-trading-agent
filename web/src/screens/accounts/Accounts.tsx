// 「계좌·보유」 통합 화면 — 원화·달러 합계와 당일 손익, 환율 줄, 전역 일시정지·재개, 계좌 카드(계좌별 화면 링크).
// 섹션 컨테이너(id = NAV 항목)는 데이터 전에 바로 그린다. `fo-positions` 는 파생 계좌 카드 묶음(Ruling P2) — 각 카드에서
// 계좌별 화면으로 간다. fo 계좌가 없을 때만 hidden. 전역 일시정지는 합계(/accounts/overview) 성공 여부와 무관하게 늘 그린다.
import { useState } from "react";
import { Link } from "react-router-dom";
import { Card } from "../../components/Card";
import { Chip } from "../../components/Chip";
import { Confirm } from "../../components/Confirm";
import { Empty } from "../../components/Empty";
import { errorText, patchJson } from "../../lib/api";
import { fmtMoney } from "../../lib/format";
import { useResource } from "../../lib/hooks";
import { AccountCardView } from "./AccountCard";
import { cardRows, resumeNeedsConfirm, totals, type Account, type Overview } from "./accounts.ts";

export function Accounts() {
  const overview = useResource<Overview>("/accounts/overview", undefined, ["accounts", "positions", "control"]);
  const accounts = useResource<Account[]>("/accounts", undefined, ["accounts", "approvals", "control"]);
  // 전역 paused 는 /accounts 행마다 같은 값으로 실린다. 계좌가 없으면 합계 쪽 값, 둘 다 모르면 false(일시정지 버튼 — 안전한 방향).
  const paused = accounts.data?.length ? accounts.data.some((a) => a.paused) : (overview.data?.paused ?? false);
  const spotCards = accounts.data ? cardRows(accounts.data.filter((a) => a.lane !== "fo")) : [];
  const foAccounts = accounts.data ? accounts.data.filter((a) => a.lane === "fo") : null;

  return (
    <div className="screen" data-screen="accounts">
      <h1 className="screen-title">계좌·보유</h1>

      <section id="overview" className="screen-section">
        <h2>통합</h2>
        {overview.error ? (
          <Empty title="합계를 불러오지 못했습니다">{overview.error.message}</Empty>
        ) : overview.data ? (
          <TotalsRow data={overview.data} />
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
          accounts.data.length ? (
            <div className="account-grid">
              {spotCards.map((c) => (
                <AccountCardView key={c.id} card={c} />
              ))}
            </div>
          ) : (
            <Empty title="계좌 없음" />
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
              <FoAccountCard key={a.id} account={a} />
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

/** 파생 계좌 카드 — id·모의/실전·모드(+메모)·NAV/현금·계좌별 화면 링크. 보유 상세는 계좌별 화면 「파생 보유」. */
function FoAccountCard({ account: a }: { account: Account }) {
  const money = (v: Account["nav"] | undefined) => fmtMoney(v ?? null, a.currency === "USD" ? "USD" : "KRW");
  const live = a.env !== "paper";
  return (
    <Card title={a.id} right={<Chip tone={live ? "live" : "muted"}>{live ? "실전" : "모의"}</Chip>}>
      <p className="account-mode">
        모드 {MODE_LABEL[a.mode] ?? a.mode}
        {a.mode_note ? <span className="account-mode-note"> · {a.mode_note}</span> : null}
      </p>
      <dl className="account-stats">
        <div>
          <dt>NAV</dt>
          <dd>{money(a.nav)}</dd>
        </div>
        <div>
          <dt>현금</dt>
          <dd>{money(a.cash)}</dd>
        </div>
      </dl>
      <Link to={`/accounts/${encodeURIComponent(a.id)}`}>계좌별 화면 ›</Link>
    </Card>
  );
}

function TotalsRow({ data }: { data: Overview }) {
  const t = totals(data);
  return (
    <div className="totals-grid">
      <Card title="원화 합계">
        <p className="totals-value">{t.krw}</p>
        <p className="totals-pnl">당일 손익 {t.krwPnl}</p>
      </Card>
      <Card title="달러 합계">
        <p className="totals-value">{t.usd}</p>
        <p className="totals-pnl">당일 손익 {t.usdPnl}</p>
      </Card>
      <p className="totals-fx">{t.converted ? `${t.fxLine} → ${t.converted}` : t.fxLine}</p>
    </div>
  );
}

/** 일시정지는 확인만, 재개는 실전 auto 계좌가 있으면 그 id 재입력 → `confirm`. 계좌 목록 전에는 버튼을 끈다(confirm 대상 판단 불가). */
function PauseControl({ paused, confirmId, ready, onDone }: { paused: boolean; confirmId: string | null; ready: boolean; onDone(): void }) {
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const label = paused ? "재개" : "일시정지";

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
        body={paused ? "모든 계좌의 자동 매매를 다시 켭니다." : "모든 계좌의 새 주문을 멈춥니다."}
        live={paused && confirmId ? { accountId: confirmId } : undefined}
        onConfirm={(confirm) => void submit(confirm)}
        onClose={() => setOpen(false)}
      />
    </div>
  );
}
