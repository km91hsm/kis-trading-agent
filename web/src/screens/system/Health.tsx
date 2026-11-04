// 헬스 — GET /system/health(단계 칩·사유·버전·기능)와 GET /system/status(텔레그램 /status 본문·일시정지·제어 키).
import { Chip, Chips } from "../../components/Chip";
import { useResource } from "../../lib/hooks";
import type { Health as HealthResp } from "../../lib/health";
import { healthDot } from "../../lib/health";
import { body } from "../performance/ResourceView";

type Status = { text: string; paused: boolean | null; flags: string[] };
const LEVEL_TEXT = { ok: "정상", warn: "주의", fail: "장애" } as const;

export function Health() {
  const health = useResource<HealthResp>("/system/health", undefined, ["daemon", "control"]);
  const status = useResource<Status>("/system/status", undefined, ["control"]);
  return (
    <>
      {body(health, "헬스", (h) => {
        const level = healthDot(h.level);
        return (
          <>
            <p>
              <Chip tone={level}>{LEVEL_TEXT[level]}</Chip> {h.version ? <span className="data-muted">버전 {h.version}</span> : null}
            </p>
            {h.reasons.length ? (
              <ul className="sys-lines">
                {h.reasons.map((r, i) => (
                  <li key={i}>
                    <Chip tone={healthDot(r.level)}>{r.area}</Chip> {r.text}
                  </li>
                ))}
              </ul>
            ) : null}
            {h.features?.length ? <Chips items={h.features} max={20} /> : null}
          </>
        );
      })}
      {body(status, "상태", (s) => (
        <>
          <p>
            {s.paused ? <Chip tone="warn">일시정지</Chip> : null} {s.flags.length ? <Chips items={s.flags} max={10} /> : null}
          </p>
          <pre className="sys-pre">{s.text}</pre>
        </>
      ))}
    </>
  );
}
