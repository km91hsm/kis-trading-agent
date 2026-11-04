// 조작 이력 — GET /system/control-log?limit=100(최신순)을 「시각 · 채널 · 누가 · 키 · 전 → 후」 한 줄씩.
import { Empty } from "../../components/Empty";
import { useResource } from "../../lib/hooks";
import { body } from "../performance/ResourceView";
import { controlRows, type ControlEntry } from "./system.ts";

const PARAMS = { limit: "100" };

export function ControlLog() {
  const res = useResource<ControlEntry[]>("/system/control-log", PARAMS, ["control"]);
  return body(res, "조작 이력", (log) =>
    log.length ? (
      <ol className="sys-lines sys-log">
        {controlRows(log).map((r) => (
          <li key={r.id}>{r.line}</li>
        ))}
      </ol>
    ) : (
      <Empty title="조작 이력 없음" />
    ),
  );
}
