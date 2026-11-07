// 공급자별 구독 사용률. Codex의 시간 창은 서버가 실제로 제공한 기간으로 구분한다.
import { useCodexUsage } from "../../lib/codex-usage";
import { useResource } from "../../lib/hooks";
import { codexLimitReason, codexUsageChip, usageChip, type UsageResp } from "../../lib/usage";
import "./usage-chips.css";

export function UsageChips({ now }: { now: Date }) {
  const { data } = useResource<UsageResp>("/system/usage", undefined, ["usage"]);
  const codex = useCodexUsage();
  const rl = data?.rate_limits ?? null;
  const chips = [
    { key: "5h", label: "5시간", ...usageChip(rl?.five_hour ?? null, now) },
    { key: "7d", label: "7일", ...usageChip(rl?.seven_day ?? null, now) },
  ];
  return (
    <div className="provider-usage-chips">
      <div className="usage-chips" aria-label="Claude 사용률">
        <span className="usage-provider">Claude</span>
        {chips.map((c) => (
          <span key={c.key} className="usage-chip" data-usage-chip={c.key} data-level={c.level}>
            <span className="usage-chip-label">{c.label}</span> <span className="num">{c.text}</span>
          </span>
        ))}
      </div>
      <div className="usage-chips" aria-label="Codex 사용률" title={codex.data?.rate_limits.reason ? codexLimitReason(codex.data.rate_limits.reason) : undefined}>
        <span className="usage-provider">Codex</span>
        {([
          { key: "5h", label: "5시간", window: codex.data?.rate_limits.five_hour ?? null },
          { key: "7d", label: "7일", window: codex.data?.rate_limits.seven_day ?? null },
        ]).map(({ key, label, window }) => {
          const chip = codexUsageChip(window, codex.data?.rate_limits, now);
          return <span key={key} className="usage-chip" data-codex-usage-chip={key} data-level={chip.level}>
            <span className="usage-chip-label">{label}</span> <span className="num">{codex.loading && !codex.data ? "조회 중" : chip.text}</span>
          </span>;
        })}
      </div>
    </div>
  );
}
