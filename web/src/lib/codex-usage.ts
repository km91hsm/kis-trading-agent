import { useEffect } from "react";
import { useResource } from "./hooks";
import type { CodexUsageResp } from "./usage";

/** 외부 Codex 앱 사용은 원장 SSE에 없으므로 보이는 동안 1분마다 별도로 확인한다. */
export function useCodexUsage() {
  const resource = useResource<CodexUsageResp>("/system/codex-usage", undefined, ["usage", "control"]);
  const { reload } = resource;
  useEffect(() => {
    const refresh = () => { if (document.visibilityState !== "hidden") reload(); };
    const timer = setInterval(refresh, 60000);
    document.addEventListener("visibilitychange", refresh);
    return () => { clearInterval(timer); document.removeEventListener("visibilitychange", refresh); };
  }, [reload]);
  if (resource.error && resource.data) {
    const limits = resource.data.rate_limits;
    const hasObservation = limits.five_hour !== null || limits.seven_day !== null;
    return {
      ...resource,
      data: { ...resource.data, rate_limits: { ...limits, status: hasObservation ? "stale" as const : "unavailable" as const, reason: "connection_error" } },
    };
  }
  return resource;
}
