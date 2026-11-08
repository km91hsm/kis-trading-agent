// 사용량 — 마운트 때 한 번(StrictMode 에서도 ref 로 1회)과 「새로고침」 때 POST /system/usage/ensure-fresh(토큰 없음),
// 「지금 확인」은 usage_probe 잡 요청(RunJobButton). 5시간·7일(·지출 한도 — 관측 있을 때만) 미터, 사용량 조절 한 줄·켜기/끄기
// (Confirm → PATCH /settings/usage-throttle), 창 집계 Stats(세션·비용·오류·평균 소요), kind 별 상한 표, note.
import { useCallback, useEffect, useRef, useState } from "react";
import { Confirm } from "../../components/Confirm";
import { RunJobButton } from "../../components/RunJobButton";
import { Stats } from "../../components/Stats";
import { Table, type Column } from "../../components/Table";
import { errorText, patchJson, postPublic } from "../../lib/api";
import { useCodexUsage } from "../../lib/codex-usage";
import { termLabel } from "../../lib/glossary";
import { useNow, useResource } from "../../lib/hooks";
import type { UsageResp, UsageWindow } from "../../lib/usage";
import { body } from "../performance/ResourceView";
import { CapEditor } from "./CapEditor";
import { CodexUsage } from "./CodexUsage";
import { throttleLine, usageMeters } from "./system.ts";

type Fresh = { fresh?: boolean; age_s?: number; skipped?: string; throttled?: boolean; queued?: boolean; already?: boolean };
type CapRow = UsageResp["caps_by_kind"][number];

function freshText(r: Fresh | undefined): string {
  if (!r) return "";
  if (r.fresh) return "최신 관측입니다";
  if (r.skipped) return `새 관측 건너뜀: ${r.skipped}`;
  if (r.throttled) return "방금 확인했습니다 — 잠시 뒤 다시";
  if (r.queued) return r.already ? "새 관측이 이미 대기 중입니다" : "새 관측을 요청했습니다";
  return "";
}

const CAP_COLUMNS: Column<CapRow>[] = [
  { key: "kind", label: "세션 종류", width: "50%", render: (r) => termLabel(`session.${r.kind}`) },
  { key: "used", label: "오늘 사용", width: "25%", align: "num" },
  { key: "cap", label: "상한", width: "25%", align: "num", render: (r) => (r.cap === null ? "—" : String(r.cap)) },
];

/** 「$1.50 · 오류 1 · 평균 42초」 — 0 인 항목은 뺀다(비용은 항상). */
function windowSub(w: UsageWindow): string {
  const parts = [`$${w.cost_usd.toFixed(2)}`];
  if (w.errors) parts.push(`오류 ${w.errors}`);
  if (w.avg_duration_s !== null && w.avg_duration_s !== undefined) parts.push(`평균 ${Math.round(w.avg_duration_s)}초`);
  return parts.join(" · ");
}

export function Usage() {
  const usage = useResource<UsageResp>("/system/usage", undefined, ["usage"]);
  const codex = useCodexUsage();
  const now = useNow(30000);
  const { reload } = usage;
  const [fresh, setFresh] = useState<string | null>(null);
  const [confirming, setConfirming] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const started = useRef(false);

  const ensure = useCallback(async () => {
    try {
      const r = await postPublic<Fresh>("/system/usage/ensure-fresh");
      setFresh(freshText(r));
      reload();
    } catch (e) {
      setFresh(errorText(e));
    }
  }, [reload]);

  useEffect(() => {
    if (started.current) return;
    started.current = true;
    void ensure();
  }, [ensure]);

  const toggle = async (enabled: boolean) => {
    setConfirming(false);
    setError(null);
    try {
      await patchJson("/settings/usage-throttle", { enabled });
      reload();
    } catch (e) {
      setError(errorText(e));
    }
  };

  return (
    <>
      <div className="perf-filters">
        <button type="button" className="btn" onClick={() => { void ensure(); codex.reload(); }}>
          새로고침
        </button>
        <RunJobButton kind="usage_probe" label="지금 확인" trading={false} realAccounts={[]} onDone={() => setFresh("새 관측을 요청했습니다 — 데몬이 곧 처리합니다")} />
        {fresh ? <span className="data-muted">{fresh}</span> : null}
      </div>
      {body(usage, "사용량", (u) => (
        <>
          <h3 className="perf-sub">Claude 사용량</h3>
          <ul className="sys-meters">
            {usageMeters(u, now)
              .filter((m) => m.key !== "spend" || m.used_pct !== null)
              .map((m) => (
                <li key={m.key} className="sys-meter" data-meter={m.key} data-level={m.level}>
                  <span className="sys-meter-label">{m.label}</span>
                  {m.used_pct === null ? (
                    <span className="num">—</span>
                  ) : (
                    <>
                      <meter min={0} max={100} low={70} high={90} optimum={0} value={m.used_pct} aria-label={`${m.label} 사용률`} aria-valuenow={m.used_pct} />
                      <span className="num">{Math.round(m.used_pct)}%</span>
                    </>
                  )}
                  <span className="data-muted">
                    {[m.observed ? `관측 ${m.observed}` : null, m.resets, m.source ? termLabel(`usage.${m.source}`) : null].filter(Boolean).join(" · ")}
                  </span>
                </li>
              ))}
          </ul>
          <p className="perf-note">
            {throttleLine(u.throttle)}{" "}
            <button type="button" className="btn" onClick={() => setConfirming(true)}>
              {u.throttle.enabled ? "조절 끄기" : "조절 켜기"}
            </button>
          </p>
          {error ? (
            <p className="data-error" role="alert">
              {error}
            </p>
          ) : null}
          <Stats
            cols={3}
            items={[
              { label: "오늘 세션", value: `${u.windows.today.sessions} / ${u.windows.today.cap}` },
              { label: "5시간", value: `${u.windows.h5.sessions}회`, sub: windowSub(u.windows.h5) },
              { label: "7일", value: `${u.windows.d7.sessions}회`, sub: windowSub(u.windows.d7) },
            ]}
          />
          {u.session_cap ? (
            <CapEditor
              label="Claude 하루 세션 제한"
              value={u.session_cap.value}
              source={u.session_cap.source}
              defaultValue={u.session_cap.default}
              min={1}
              max={1000}
              unit="회"
              onSave={async (cap) => { await patchJson("/settings/session-cap", { cap }); reload(); }}
            />
          ) : null}
          <Table columns={CAP_COLUMNS} rows={u.caps_by_kind} rowKey={(r) => r.kind} empty="kind 별 상한 없음" />
          {u.note ? <p className="perf-note">{u.note}</p> : null}
          <Confirm
            open={confirming}
            title={u.throttle.enabled ? "사용량 조절 끄기" : "사용량 조절 켜기"}
            body={<p>{u.throttle.enabled ? "사용률이 높아도 잡을 줄이지 않습니다." : "사용률이 높으면 잡을 줄이거나 멈춥니다."}</p>}
            onConfirm={() => void toggle(!u.throttle.enabled)}
            onClose={() => setConfirming(false)}
          />
        </>
      ))}
      <CodexUsage resource={codex} now={now} />
    </>
  );
}
