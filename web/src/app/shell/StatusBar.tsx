// 상단 상태 띠 — 상태 점·첫 사유·「N초 전 갱신」·시계 어긋남, 사용률 칩, 시계, 테마 토글.
// health 는 SSE 키(daemon·runs·usage·control)로 다시 읽고, 끊겼을 때는 sse 의 30초 폴백에 더해 60초마다 직접 다시 읽는다.
import { useEffect } from "react";
import { Tooltip } from "../../components/Tooltip";
import { clockSkewText, serverOffsetMs } from "../../lib/clock";
import { agoText } from "../../lib/format";
import { healthDot, healthSummary, type Health } from "../../lib/health";
import { useNow, useResource } from "../../lib/hooks";
import { Clock } from "./Clock";
import { ThemeToggle } from "./ThemeToggle";
import { UsageChips } from "./UsageChips";

export const HEALTH_REFRESH_MS = 60000;

const LEVEL_TEXT = { ok: "정상", warn: "주의", fail: "장애" } as const;

export function StatusBar() {
  const { data: h, error, reload } = useResource<Health>("/system/health", undefined, ["daemon", "runs", "usage", "control"]);
  const now = useNow(1000);

  useEffect(() => {
    const id = setInterval(reload, HEALTH_REFRESH_MS);
    return () => clearInterval(id);
  }, [reload]);

  const skew = clockSkewText(serverOffsetMs());
  const level = h ? healthDot(h.level) : null;
  const summary = !h && error ? "상태를 불러오지 못했습니다" : healthSummary(h, null);
  const reasons = h?.reasons.map((r) => r.text.split("\n")[0]).join(" / ") ?? "";

  return (
    <header className="statusbar" data-statusbar>
      <div className="statusbar-health" data-health={level ?? "pending"} role="status">
        <span className="dot" aria-hidden="true" />
        <span className="sr-only">{level ? LEVEL_TEXT[level] : "확인 중"}</span>
        {reasons ? (
          <Tooltip text={reasons}>
            <span className="statusbar-summary">{summary}</span>
          </Tooltip>
        ) : (
          <span className="statusbar-summary">{summary}</span>
        )}
        {h ? <span className="statusbar-ago num">{agoText(h.checked_at, now)} 갱신</span> : null}
        {skew ? <span className="statusbar-skew">{skew}</span> : null}
      </div>
      <UsageChips now={now} />
      <Clock />
      <ThemeToggle />
    </header>
  );
}
