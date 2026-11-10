import { useEffect, useId, useRef, useState } from "react";
import { ApiError, errorText, patchJson } from "../../lib/api";
import { now as serverNow } from "../../lib/clock";
import { fmtPct } from "../../lib/format";
import { useResource, type Resource } from "../../lib/hooks";
import { entryName, marketName, policyLimits, policyNames, policyTime, stanceName, type PolicyMode, type RiskMarket, type RiskPolicyResponse } from "./risk-policy";
import "./risk-policy.css";

/** SSE 지문을 바꾸지 않는 시간 만료도 서버 보정 시각에 맞춰 다시 확인한다. */
function useRiskPolicy(): Resource<RiskPolicyResponse> {
  const resource = useResource<RiskPolicyResponse>("/settings/risk-policy", undefined, ["control", "market_views", "accounts"]);
  const checkedExpiries = useRef(new Set<string>());
  const { data, error, reload } = resource;
  useEffect(() => {
    if (!data || error) return;
    const expiries = data.markets.flatMap((item) => {
      const at = Date.parse(item.policy.expires_at ?? "");
      return item.policy.source === "manual" && Number.isFinite(at)
        ? [{ at, key: `${item.market}:${item.policy.mode}:${at}` }] : [];
    });
    let timer: ReturnType<typeof setTimeout> | undefined;
    const schedule = () => {
      const current = serverNow().getTime();
      const due = expiries.filter((expiry) => expiry.at <= current && !checkedExpiries.current.has(expiry.key));
      if (due.length) {
        // 같은 만료 응답을 다시 받아도 즉시 재조회는 한 번만 한다.
        due.forEach((expiry) => checkedExpiries.current.add(expiry.key));
        reload();
        return;
      }
      const next = Math.min(...expiries.filter((expiry) => expiry.at > current).map((expiry) => expiry.at));
      if (Number.isFinite(next)) timer = setTimeout(schedule, Math.min(next - current, 2_147_483_647));
    };
    schedule();
    return () => clearTimeout(timer);
  }, [data, error, reload]);
  useEffect(() => {
    const visible = () => { if (document.visibilityState === "visible") reload(); };
    document.addEventListener("visibilitychange", visible);
    return () => document.removeEventListener("visibilitychange", visible);
  }, [reload]);
  const expired = data?.markets.some((item) => item.policy.source === "manual" && Date.parse(item.policy.expires_at ?? "") <= serverNow().getTime());
  // 만료 후 재확인 중이거나 서버가 지난 수동 정책을 돌려주면 실효 정책으로 단정하지 않는다.
  return expired && !error ? { ...resource, error: new ApiError(0, "수동 정책이 만료되었습니다. 현재 정책을 다시 확인해야 합니다.") } : resource;
}

function PolicyUnavailable({ resource }: { resource: Resource<RiskPolicyResponse> }) {
  if (resource.error) return <div className="risk-policy-empty" role="alert"><strong>현재 매수 정책을 확인할 수 없습니다.</strong><p>{errorText(resource.error)}</p><button type="button" className="btn" onClick={resource.reload}>다시 조회</button></div>;
  if (!resource.data) return <p className="risk-policy-empty" role="status">현재 매수 정책을 확인하고 있습니다.</p>;
  if (!resource.data.markets?.length) return <p className="risk-policy-empty">매수 허용 여부를 확인할 자료가 없습니다. 운영 시장과 국면 수집 상태를 확인하세요.</p>;
  return null;
}

function ActualPolicy({ item }: { item: RiskMarket }) {
  const label = marketName(item.market);
  const limits = policyLimits(item.effective);
  return <div className="risk-policy-actual" aria-label={`${label} 실제 매수 정책`} data-entry={item.effective.entry}>
    <span className="risk-policy-eyebrow">실제 적용되는 일반 현물 매수 정책</span>
    <strong className="risk-policy-entry">{entryName(item.effective.entry)}</strong>
    <p>{item.effective.reason || "적용 사유를 확인할 수 없습니다."}</p>
    {limits && <p className="risk-policy-limits">{limits}</p>}
    <p className="risk-policy-note">계좌 모드·일시정지·킬스위치·일일손실 등 기존 제한도 함께 적용됩니다.</p>
  </div>;
}

export function RiskPolicySummary({ historical = false, compact = false }: { historical?: boolean; compact?: boolean }) {
  const resource = useRiskPolicy();
  return <section className={`risk-policy-summary${compact ? " risk-policy-summary-compact" : ""}`} aria-label="현재 매수 정책">
    <div className="risk-policy-heading"><div><h2>현재 매수 정책</h2><p>{historical ? "과거 조회 날짜와 관계없이 지금 적용되는 정책입니다." : "시장의 판단과 실제 신규 매수 범위를 확인하세요."}</p></div><a href="/system/features#regime">정책 상세·변경 →</a></div>
    <PolicyUnavailable resource={resource} />
    {!resource.error && resource.data?.markets?.length ? <div className="risk-policy-summary-grid">{resource.data.markets.map((item) => <article className="risk-policy-summary-card" key={item.market}>
      <h3>{marketName(item.market)} <span>{item.market}</span></h3>
      <ActualPolicy item={item} />
      <p className="risk-policy-note">AI 원본 판단: {stanceName(item.ai_view?.stance)}{item.ai_view?.confidence != null ? ` · 확신도 ${fmtPct(item.ai_view.confidence, 0)}` : " · 확신도 자료 없음"}</p>
    </article>)}</div> : null}
  </section>;
}

const MODES: { mode: PolicyMode; label: string; help: string }[] = [
  { mode: "AUTO", label: "자동 · 시장 판단 따르기", help: "정량 국면과 AI 판단에 따른 기본 정책" },
  { mode: "RISK_OFF", label: "방어 · 신규 매수 차단", help: "일반 현물 신규·추가 매수 중단" },
  { mode: "RISK_ON", label: "제한적 매수 · 10%·2종목 이내", help: "기존 한도가 더 작으면 그 한도를 적용" },
];

function PolicyEditor({ item, reload }: { item: RiskMarket; reload(): void }) {
  const label = marketName(item.market);
  const id = useId();
  const [selected, setSelected] = useState<PolicyMode | null>(null);
  const [reason, setReason] = useState("");
  const [confirmations, setConfirmations] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);
  const messageRef = useRef<HTMLParagraphElement>(null);
  const choicesRef = useRef<Partial<Record<PolicyMode, HTMLButtonElement | null>>>({});
  const realAccounts = item.accounts.filter((account) => account.env === "real");
  const ready = selected !== null && !!reason.trim() && realAccounts.every((account) => confirmations[account.id] === account.id);
  useEffect(() => { if (error || success) messageRef.current?.focus(); }, [error, success]);
  const save = async () => {
    if (!ready || busy || selected === null) return;
    setBusy(true); setError(null); setSuccess(null);
    try {
      await patchJson<RiskPolicyResponse>(`/settings/risk-policy/${encodeURIComponent(item.market)}`, {
        mode: selected, reason: reason.trim(), ...(realAccounts.length ? { confirmations: Object.fromEntries(realAccounts.map((account) => [account.id, confirmations[account.id]])) } : {}),
      });
      setSelected(null); setReason(""); setConfirmations({});
      setSuccess(`${label} 매수 정책을 저장했습니다. 최신 적용 상태를 다시 조회합니다.`);
      reload();
    } catch (e) { setError(errorText(e)); }
    finally { setBusy(false); }
  };
  return <div className="risk-policy-editor" aria-label={`${label} 정책 변경`} aria-busy={busy}>
    <h4>일반 현물 매수 정책 변경</h4>
    <div className="risk-policy-choices" role="group" aria-label={`${label} 정책 선택`}>{MODES.map(({ mode, label: option, help }) => <button key={mode} ref={(node) => { choicesRef.current[mode] = node; }} type="button" className="risk-policy-choice" aria-pressed={selected === mode} disabled={busy} onClick={() => { setSelected(mode); setError(null); setSuccess(null); }}><strong>{option}</strong><span>{help}</span></button>)}</div>
    {selected && <div className="risk-policy-form">
      <p className="risk-policy-change">{policyNames[item.policy.mode]} → <strong>{policyNames[selected]}</strong></p>
      <p className="risk-policy-note">수동 정책은 해당 시장의 운영일에만 적용되며 다음 운영일에 자동으로 복귀합니다. 저장해도 과거 HOLD 판단을 다시 실행하거나 즉시 주문하지 않습니다.</p>
      <label htmlFor={`${id}-reason`}>변경 사유 <span>(필수)</span></label>
      <textarea id={`${id}-reason`} aria-label={`${label} 변경 사유`} value={reason} onChange={(event) => setReason(event.target.value)} disabled={busy} rows={2} required />
      {realAccounts.length > 0 && <fieldset><legend>영향을 받는 실전 계좌 확인</legend><p className="risk-policy-note">각 계좌 id를 그대로 입력하세요. 자동 복귀를 포함한 모든 변경에 필요합니다.</p>{realAccounts.map((account, index) => <div className="risk-policy-confirm" key={account.id}><label htmlFor={`${id}-account-${index}`}><code>{account.id}</code> 확인</label><input id={`${id}-account-${index}`} aria-label={`${account.id} 확인`} value={confirmations[account.id] ?? ""} onChange={(event) => setConfirmations((previous) => ({ ...previous, [account.id]: event.target.value }))} autoComplete="off" spellCheck={false} disabled={busy} /></div>)}</fieldset>}
      <div className="risk-policy-actions"><button type="button" className="btn" disabled={busy} onClick={() => { choicesRef.current[selected]?.focus(); setSelected(null); setReason(""); setConfirmations({}); setError(null); }}>취소</button><button type="button" className="btn btn-primary" disabled={!ready || busy} onClick={() => void save()}>{busy ? "저장 중…" : `${label} 정책 저장`}</button></div>
    </div>}
    {(error || success) && <p ref={messageRef} tabIndex={-1} role={error ? "alert" : "status"} className={error ? "risk-policy-error" : "risk-policy-success"}>{error || success}</p>}
  </div>;
}

const SIGNALS: Record<string, string> = { ma_below: "이동평균 아래", momentum_weak: "모멘텀 약화", vol_high: "변동성 확대", agent_risk_off: "AI 방어 판단", flow_weak: "수급 약화" };
const VALUES: Record<string, string> = { close: "종가", ma: "이동평균", momentum: "모멘텀", realized_vol: "실현 변동성", vix: "VIX" };
function MarketEvidence({ item }: { item: RiskMarket }) {
  const label = marketName(item.market);
  return <div className="risk-policy-evidence">
    <section aria-label={`${label} AI 원본 판단`}><h4>AI 원본 판단</h4><p><strong>{stanceName(item.ai_view?.stance)}</strong> · 확신도 {item.ai_view?.confidence != null ? fmtPct(item.ai_view.confidence, 0) : "자료 없음"}</p><p>{item.ai_view?.rationale || "AI 판단 사유가 아직 없습니다."}</p><p className="risk-policy-note">판단 기준일: {policyTime(item.ai_view?.date, item.market)} · 수동 정책을 저장해도 원본 판단과 확신도는 유지됩니다.</p></section>
    <section><h4>정량 시장 상태</h4><p><strong>{{ bull: "상승", neutral: "중립", bear: "하락" }[item.regime ?? ""] ?? "자료 없음"}</strong> · 기준일 {policyTime(item.asof, item.market)}</p><p className="risk-policy-note">회복 조건 진행: {item.streak == null ? "자료 없음" : `${item.streak}일`}{item.exit_streak_days != null ? ` / ${item.exit_streak_days}일` : ""}</p>
      <dl className="risk-policy-signals">{Object.entries(SIGNALS).map(([key, name]) => <div key={key}><dt>{name}</dt><dd data-signal={item.signals?.[key] === true ? "risk" : undefined}>{item.signals?.[key] === true ? "위험 신호" : item.signals?.[key] === false ? "미해당" : "자료 없음"}</dd></div>)}</dl>
      <details><summary>지표 수치 보기</summary><dl className="risk-policy-values">{Object.entries(VALUES).map(([key, name]) => <div key={key}><dt>{name}</dt><dd>{item.values?.[key] == null ? "자료 없음" : key === "momentum" || key === "realized_vol" ? fmtPct(item.values[key]) : item.values[key].toLocaleString("ko-KR", { maximumFractionDigits: 2 })}</dd></div>)}</dl></details>
    </section>
  </div>;
}

export function RiskPolicy() {
  const resource = useRiskPolicy();
  return <div className="risk-policy-detail">
    <p className="risk-policy-intro">시장의 원본 판단을 확인하고, 일반 현물 신규·추가 매수 범위를 시장별로 정합니다. 위험 청산·인버스 헤지·현금 운용·단타·파생 정책은 각각 따릅니다.</p>
    <PolicyUnavailable resource={resource} />
    {!resource.error && resource.data?.markets?.map((item) => <article className="risk-policy-market" key={item.market}>
      <header className="risk-policy-market-heading"><h3>{marketName(item.market)} 시장 <span>{item.market}</span></h3><span className="risk-policy-source">{item.policy.source === "manual" ? "수동 운영 정책" : "자동 정책"} · {policyNames[item.policy.mode]}</span></header>
      <ActualPolicy item={item} />
      <dl className="risk-policy-meta"><div><dt>설정 사유</dt><dd>{item.policy.reason || "자동 시장 판단에 따릅니다."}</dd></div><div><dt>유효 기간</dt><dd>{item.policy.expires_at ? `${policyTime(item.policy.expires_at, item.market)}까지` : item.policy.mode === "AUTO" ? "수동 정책 없음 · 자동 적용" : "만료 시각 자료 없음"}</dd></div><div><dt>정책 기준일</dt><dd>{policyTime(item.policy.date, item.market)}</dd></div></dl>
      <MarketEvidence item={item} />
      <PolicyEditor item={item} reload={resource.reload} />
      <details className="risk-policy-history"><summary>정책 변경 이력 {item.history.length}건</summary>{item.history.length ? <ol>{item.history.map((entry, index) => <li key={`${entry.at}-${index}`}><p><strong>{policyNames[entry.mode]}</strong> · {policyTime(entry.at, item.market)} · {entry.actor || "변경자 미기록"}</p><p>{entry.reason}</p><p className="risk-policy-note">{entry.expires_at ? `만료 ${policyTime(entry.expires_at, item.market)}` : "자동 정책"}</p></li>)}</ol> : <p>아직 수동 변경 이력이 없습니다.</p>}</details>
    </article>)}
  </div>;
}
