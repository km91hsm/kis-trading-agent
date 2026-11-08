import { useState } from "react";
import { Confirm } from "../../components/Confirm";
import { Stats } from "../../components/Stats";
import { errorText, patchJson } from "../../lib/api";
import { fmtInt, fmtTime } from "../../lib/format";
import type { Resource } from "../../lib/hooks";
import { codexLimitReason, codexUsageChip, resetsIn, type CodexThrottle, type CodexUsageResp } from "../../lib/usage";
import { CapEditor } from "./CapEditor";
import { body } from "../performance/ResourceView";

/** 「사용량 조절 켜짐 · …」 한 줄 — 켜져 있고 초과면 사유와 함께 새 분석은 Claude 가 맡는다고 알린다. */
function codexThrottleLine(t: CodexThrottle): string {
  const where = t.source === "control" ? " (화면 설정)" : "";
  if (!t.enabled) return `사용량 조절 꺼짐${where} · 사용률과 관계없이 하루 제한까지 Codex 를 씁니다`;
  if (t.state === "over") return `사용량 조절 켜짐${where} · ${t.reason ?? "사용률·제한 초과"} — 새 분석은 Claude 가 강세·약세를 맡습니다`;
  return `사용량 조절 켜짐${where} · 5시간·7일 사용률 ${Math.round(t.hard_pct * 100)}% 이상이거나 하루 제한에 닿으면 새 분석은 Claude 가 강세·약세를 맡습니다`;
}

export function CodexUsage({ resource, now }: { resource: Resource<CodexUsageResp>; now: Date }) {
  const [confirming, setConfirming] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const toggle = async (enabled: boolean) => {
    setConfirming(false);
    setError(null);
    try {
      await patchJson("/settings/codex-throttle", { enabled });
      resource.reload();
    } catch (e) {
      setError(errorText(e));
    }
  };
  return <section aria-label="Codex 사용량">
    <h3 className="perf-sub">Codex 사용량</h3>
    {resource.error && resource.data ? <p className="perf-note" role="status">새 조회 확인 불가 · 이전 프로그램 기록을 표시합니다.</p> : null}
    {body(resource.data ? { ...resource, error: null } : resource, "Codex 사용량", (data) => {
      const limits = data.rate_limits;
      const usage = data.usage;
      return <>
        <p className="perf-note">토론 참여 {data.enabled ? "켜짐" : "꺼짐"} · 구독 한도는 로그인한 Codex 계정 기준입니다.</p>
        {data.throttle ? <p className="perf-note">
          {codexThrottleLine(data.throttle)}{" "}
          <button type="button" className="btn" onClick={() => setConfirming(true)}>{data.throttle.enabled ? "조절 끄기" : "조절 켜기"}</button>
        </p> : null}
        {error ? <p className="data-error" role="alert">{error}</p> : null}
        {data.throttle ? <Confirm
          open={confirming}
          title={data.throttle.enabled ? "Codex 사용량 조절 끄기" : "Codex 사용량 조절 켜기"}
          body={<p>{data.throttle.enabled ? "사용률이 높아도 하루 제한까지 Codex 를 씁니다." : "사용률이 높거나 하루 제한에 닿으면 새 분석은 Claude 가 강세·약세를 맡습니다."}</p>}
          onConfirm={() => void toggle(!data.throttle!.enabled)}
          onClose={() => setConfirming(false)}
        /> : null}
        {limits.status !== "ready" ? <p className="perf-note" role="status">
          {limits.status === "stale" ? "이전 관측 · 새 조회 확인 불가" : "구독 사용률 확인 불가"} · {codexLimitReason(limits.reason)}
        </p> : null}
        <ul className="sys-meters">
          {([
            { key: "5h", label: "5시간", window: limits.five_hour },
            { key: "7d", label: "7일", window: limits.seven_day },
          ]).map(({ key, label, window }) => {
            const chip = codexUsageChip(window, limits, now);
            const reset = resetsIn(window?.resets_at, now);
            return <li key={key} className="sys-meter" data-codex-meter={key} data-level={chip.level}>
              <span className="sys-meter-label">{label}</span>
              {window?.used_pct !== null && window?.used_pct !== undefined ? <>
                <meter min={0} max={100} low={70} high={90} optimum={0} value={window.used_pct} aria-label={`Codex ${label} 사용률`} aria-valuenow={window.used_pct} />
                <span className="num">{Math.round(window.used_pct)}%</span>
              </> : <span>{chip.text}</span>}
              <span className="data-muted">{reset ? `리셋까지 ${reset}` : ""}</span>
            </li>;
          })}
        </ul>
        {limits.observed_at ? <p className="perf-note">관측 {fmtTime(limits.observed_at)} · 구독 조회 결과는 최대 1분간 재사용합니다.</p> : null}
        <h4 className="perf-sub">오늘 프로그램 기록</h4>
        <Stats cols={3} items={[
          { label: "Codex 호출 / 일일 상한", value: `${usage.calls} / ${usage.daily_call_cap}회`, sub: `오류 ${usage.errors}회 · 소요 ${Math.round(usage.duration_ms / 1000)}초` },
          { label: "입력 / 출력 토큰", value: `${usage.input_tokens === null ? "미제공" : fmtInt(usage.input_tokens)} / ${usage.output_tokens === null ? "미제공" : fmtInt(usage.output_tokens)}` },
          { label: "비용", value: usage.cost_usd === null ? "미제공" : `$${usage.cost_usd.toFixed(2)}` },
        ]} />
        <CapEditor
          label="Codex 하루 호출 제한"
          value={usage.daily_call_cap}
          source={usage.cap_source}
          min={1}
          max={10000}
          unit="회"
          onSave={async (cap) => { await patchJson("/settings/codex-call-cap", { cap }); resource.reload(); }}
        />
        <p className="perf-note">프로그램 기록은 오늘 이 프로그램에서 실행한 호출만 집계합니다. 구독 전체 사용률과 범위가 다릅니다.</p>
      </>;
    })}
  </section>;
}
