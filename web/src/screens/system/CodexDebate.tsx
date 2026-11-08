import { useState } from "react";
import { Chip } from "../../components/Chip";
import { Stats } from "../../components/Stats";
import { errorText, patchJson } from "../../lib/api";
import { fmtInt } from "../../lib/format";
import { useResource } from "../../lib/hooks";
import { laneLabel } from "../../lib/trace";
import { body } from "../performance/ResourceView";

type CodexSettings = {
  enabled: boolean;
  source: string;
  model: string | null;
  available: boolean;
  readiness: string;
  effective_rounds: number;
  applies_to: string;
  roles: { bull: string; bear: string };
  lanes?: { lane: string; bull: string; bear: string }[];
  usage: { calls: number; errors: number; duration_ms: number; input_tokens: number | null; output_tokens: number | null; cost_usd: number | null; subscription_utilization: number | null; daily_call_cap?: number };
  last_error: string | null;
};

const providerName = (p: string) => (p === "codex" ? "Codex" : p === "claude" ? "Claude" : p);

export function CodexDebate() {
  const res = useResource<CodexSettings>("/settings/codex-debate", undefined, ["control", "usage"]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);
  const toggle = async (enabled: boolean) => {
    setBusy(true);
    setError(null);
    setSaved(false);
    try {
      await patchJson<CodexSettings>("/settings/codex-debate", { enabled });
      res.reload();
      setSaved(true);
    } catch (e) {
      setError(errorText(e));
    } finally {
      setBusy(false);
    }
  };
  return (
    <div className="codex-debate">
      <h3 className="perf-sub">Codex 토론 참여</h3>
      {body(res, "Codex 설정", (s) => (
        <>
          <div className="perf-filters">
            <button type="button" role="switch" aria-label="Codex 토론 참여" aria-checked={s.enabled}
              aria-describedby="codex-scope" className="btn" disabled={busy || res.loading}
              onClick={() => void toggle(!s.enabled)}>
              {busy ? "저장 중…" : s.enabled ? "켜짐 · 끄기" : "꺼짐 · 켜기"}
            </button>
            <Chip tone={s.available ? "muted" : "warn"}>{s.available ? "설치됨 · 로그인은 실행 시 확인" : "Codex 설치 필요"}</Chip>
            <span className="data-muted">{s.source === "control" ? "화면에서 설정" : "설정 파일 사용"}</span>
          </div>
          <p id="codex-scope" className="perf-note">다음 새 분석부터 적용합니다. 켜면 강세 Claude / 약세 Codex가 독립 의견과 상호 반박을 진행합니다. 진행 중이거나 저장된 결정은 기존 설정을 유지합니다.</p>
          <p className="perf-note">{s.enabled && s.lanes?.length === 2 ? `${s.lanes.map((l) => `${laneLabel(l.lane)} ${providerName(l.bull)} 강세 · ${providerName(l.bear)} 약세`).join(" / ")} · 상호 반박 ${s.effective_rounds}회 · Codex 실패 시 새 결정을 보류합니다.` : s.enabled ? `상호 반박 ${s.effective_rounds}회 · Codex 실패 시 새 결정을 보류합니다.` : "현재 토론은 Claude가 맡습니다."} 모델: {s.model ?? "Codex 기본 모델"}</p>
          <Stats cols={4} items={[
            { label: "오늘 Codex 호출 / 상한", value: `${s.usage.calls} / ${s.usage.daily_call_cap ?? "—"}회` },
            { label: "오늘 Codex 오류", value: `${s.usage.errors}회` },
            { label: "오늘 입력 / 출력 토큰", value: `${fmtInt(s.usage.input_tokens)} / ${fmtInt(s.usage.output_tokens)}` },
            { label: "오늘 소요", value: `${Math.round(s.usage.duration_ms / 1000)}초` },
          ]} />
          <p className="data-muted">구독 사용률 미제공 · 비용 미제공. Claude 사용량과 별도로 집계합니다.</p>
          {s.last_error ? <p className="perf-note" role="alert">최근 Codex 오류: {s.last_error}</p> : null}
        </>
      ))}
      {error ? <p role="alert" className="perf-note">{error}</p> : null}
      {saved ? <p role="status" className="perf-note">설정을 저장했습니다. 다음 새 분석부터 적용합니다.</p> : null}
    </div>
  );
}
