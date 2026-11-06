// 상단 상태 띠 — 상태 점·첫 사유·「N초 전 갱신」(헬스 절 링크)·시계 어긋남, 지수·환율 칩, 사용률 칩, 시계, 테마 토글.
// health 는 SSE 키(daemon·runs·usage·control)로 다시 읽고, 끊겼을 때는 sse 의 30초 폴백에 더해 60초마다 직접 다시 읽는다.
// 좁은 폭(<768)은 한 줄: 상태·시계·테마만 두고 지수·사용률 칩은 「더 보기」 details 안에.
import { useEffect } from "react";
import { Link } from "react-router-dom";
import { Tooltip } from "../../components/Tooltip";
import { clockSkewText, serverOffsetMs } from "../../lib/clock";
import { agoText } from "../../lib/format";
import { healthDot, healthSummary, type Health } from "../../lib/health";
import { useNow, useResource } from "../../lib/hooks";
import { useWide } from "../../lib/viewport";
import { Clock } from "./Clock";
import { IndexChips } from "./IndexChips";
import { ThemeToggle } from "./ThemeToggle";
import { UsageChips } from "./UsageChips";

export const HEALTH_REFRESH_MS = 60000;
export const ONE_LINE_MAX = 768;

const LEVEL_TEXT = { ok: "정상", warn: "주의", fail: "장애" } as const;

export function StatusBar() {
  const { data: h, error, reload } = useResource<Health>("/system/health", undefined, ["daemon", "runs", "usage", "control"]);
  const now = useNow(1000);
  const wide = useWide(ONE_LINE_MAX);

  useEffect(() => {
    const id = setInterval(reload, HEALTH_REFRESH_MS);
    return () => clearInterval(id);
  }, [reload]);

  const skew = clockSkewText(serverOffsetMs());
  const level = h ? healthDot(h.level) : null;
  const summary = !h && error ? "상태를 불러오지 못했습니다" : healthSummary(h, null);
  const reasons = h?.reasons.map((r) => r.text.split("\n")[0]).join(" / ") ?? "";

  const chips = (
    <>
      <IndexChips />
      <UsageChips now={now} />
    </>
  );

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
        {h ? (
          <Link to="/system#health" className="statusbar-ago num" title="헬스 절로">
            {agoText(h.checked_at, now)} 갱신
          </Link>
        ) : null}
        {skew ? <span className="statusbar-skew">{skew}</span> : null}
      </div>
      {wide ? (
        chips
      ) : (
        <details className="statusbar-more">
          <summary aria-label="지수·사용률 보기">더 보기</summary>
          <div className="statusbar-more-body">{chips}</div>
        </details>
      )}
      <Clock />
      <ThemeToggle />
    </header>
  );
}
