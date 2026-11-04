// Claude 사용률 칩 — 5시간·7일 창. 둘 다 그리고 좁은 폭(<1024)에서는 CSS 가 7일 칩을 숨긴다.
import { useResource } from "../../lib/hooks";
import { usageChip, type UsageResp } from "../../lib/usage";

export function UsageChips({ now }: { now: Date }) {
  const { data } = useResource<UsageResp>("/system/usage", undefined, ["usage"]);
  const rl = data?.rate_limits ?? null;
  const chips = [
    { key: "5h", label: "5시간", ...usageChip(rl?.five_hour ?? null, now) },
    { key: "7d", label: "7일", ...usageChip(rl?.seven_day ?? null, now) },
  ];
  return (
    <div className="usage-chips" aria-label="Claude 사용률">
      {chips.map((c) => (
        <span key={c.key} className="usage-chip" data-usage-chip={c.key} data-level={c.level}>
          <span className="usage-chip-label">{c.label}</span> <span className="num">{c.text}</span>
        </span>
      ))}
    </div>
  );
}
