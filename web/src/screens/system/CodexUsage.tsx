import { Stats } from "../../components/Stats";
import { fmtInt, fmtTime } from "../../lib/format";
import type { Resource } from "../../lib/hooks";
import { codexLimitReason, codexUsageChip, resetsIn, type CodexUsageResp } from "../../lib/usage";
import { body } from "../performance/ResourceView";

export function CodexUsage({ resource, now }: { resource: Resource<CodexUsageResp>; now: Date }) {
  return <section aria-label="Codex 사용량">
    <h3 className="perf-sub">Codex 사용량</h3>
    {resource.error && resource.data ? <p className="perf-note" role="status">새 조회 확인 불가 · 이전 프로그램 기록을 표시합니다.</p> : null}
    {body(resource.data ? { ...resource, error: null } : resource, "Codex 사용량", (data) => {
      const limits = data.rate_limits;
      const usage = data.usage;
      return <>
        <p className="perf-note">토론 참여 {data.enabled ? "켜짐" : "꺼짐"} · 구독 한도는 로그인한 Codex 계정 기준입니다.</p>
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
        <p className="perf-note">프로그램 기록은 오늘 이 프로그램에서 실행한 호출만 집계합니다. 구독 전체 사용률과 범위가 다릅니다.</p>
      </>;
    })}
  </section>;
}
