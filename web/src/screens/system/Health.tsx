// 헬스 — GET /system/health: 머리(단계 칩·점검 시각·버전·「지금 점검」= ?live=1), 영역 격자(Stats, 영역마다 단계 칩 + 첫 사유),
// 사유 표(영역·내용·건·바로가기), 켜진 기능·제어 플래그는 details. /system/status 는 flags 만, 일시정지는 /system/pause(시각·경로)를 쓴다(원문 덤프 없음).
import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { Chip, Chips } from "../../components/Chip";
import { pauseWhen, usePause } from "../../components/PauseNotice";
import { Stats } from "../../components/Stats";
import { Table, type Column } from "../../components/Table";
import { errorText, getJson } from "../../lib/api";
import { fmtWhen } from "../../lib/format";
import { areaLabel, areaLevelText, healthDot, LEVEL_TEXT, type Health as HealthResp, type HealthReason } from "../../lib/health";
import { resolveLink } from "../../lib/nav";
import { useResource } from "../../lib/hooks";
import { body } from "../performance/ResourceView";

type Status = { text: string; paused: boolean | null; flags: string[] };

const toneOf = (level: string | null | undefined) => (level === "info" ? "muted" : healthDot(level));
const internal = (link: string) => link.startsWith("/") && !link.startsWith("//");

const REASON_COLUMNS: Column<HealthReason>[] = [
  { key: "area", label: "영역", width: "12%", align: "chip", render: (r) => <Chip>{areaLabel(r.area)}</Chip> },
  { key: "level", label: "단계", width: "10%", align: "chip", render: (r) => <Chip tone={toneOf(r.level)}>{areaLevelText(r.level)}</Chip> },
  { key: "text", label: "내용", width: "52%", long: true, render: (r) => r.text.split("\n")[0] },
  { key: "count", label: "건", width: "8%", align: "num", render: (r) => (r.count === null || r.count === undefined ? "" : String(r.count)) },
  {
    key: "link",
    label: "바로가기",
    width: "18%",
    render: (r) =>
      r.link ? (
        internal(r.link) ? (
          <Link to={resolveLink(r.link)}>보기 ›</Link>
        ) : (
          <a href={r.link}>보기 ›</a>
        )
      ) : (
        ""
      ),
  },
];

export function Health() {
  const health = useResource<HealthResp>("/system/health", undefined, ["daemon", "control"]);
  const status = useResource<Status>("/system/status", undefined, ["control"]);
  const [live, setLive] = useState<HealthResp | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // 캐시가 새로 오면(SSE·60초) 직접 점검한 결과는 버린다
  useEffect(() => setLive(null), [health.at]);

  const check = async () => {
    setBusy(true);
    setError(null);
    try {
      setLive(await getJson<HealthResp>("/system/health", { live: "1" }));
    } catch (e) {
      setError(errorText(e));
    } finally {
      setBusy(false);
    }
  };

  const flags = status.data?.flags ?? [];
  const pause = usePause();

  return body({ ...health, data: live ?? health.data }, "헬스", (h) => {
    const level = healthDot(h.level);
    const areas = Object.entries(h.areas ?? {});
    const attention = areas.filter(([, lv]) => lv === "warn" || lv === "fail").length;
    // 전체가 주의·장애인데 주의 영역이 없으면(사유만) 「문제없음」이라 쓰지 않는다
    const areaSummary = attention
      ? ` 중 ${attention}개 주의`
      : level === "ok"
        ? " 문제없음"
        : ` 점검 · 사유 ${h.reasons.length}건`;
    const firstReason = (area: string) => h.reasons.find((r) => r.area === area)?.text.split("\n")[0];
    return (
      <>
        <div className="sys-head">
          <Chip tone={level}>운영 점검: {LEVEL_TEXT[level]}</Chip>
          <span>
            데몬·잡·주문·수집 등 {areas.length}개 영역{areaSummary}
          </span>
          <span className="data-muted">
            {fmtWhen(h.checked_at)} 점검{h.version ? ` · 버전 ${h.version}` : ""}
          </span>
          <button type="button" className="btn" disabled={busy} onClick={() => void check()}>
            {busy ? "점검 중…" : "지금 점검"}
          </button>
        </div>
        <p className="sys-trade">
          {!pause.known ? (
            <Chip tone="muted">{pause.loading ? "자동매매: 확인 중" : "자동매매: 확인하지 못함"}</Chip>
          ) : pause.paused ? (
            <>
              <Chip tone="warn">자동매매: 전체 일시정지 중 — 새 매수·진입과 승인을 멈춤 · 손절·청산은 계속</Chip>{" "}
              {pauseWhen(pause) ? <span className="data-muted">{pauseWhen(pause)}</span> : null} <Link to="/accounts">재개</Link>
            </>
          ) : (
            <Chip tone="ok">자동매매: 동작 중</Chip>
          )}
        </p>
        {error ? (
          <p className="data-error" role="alert">
            {error}
          </p>
        ) : null}
        {areas.length ? (
          <Stats
            cols="auto"
            items={areas.map(([key, lv]) => ({
              id: key,
              label: areaLabel(key),
              value: <Chip tone={toneOf(lv)}>{areaLevelText(lv)}</Chip>,
              sub: firstReason(key) ?? "이상 없음",
              num: false,
            }))}
          />
        ) : null}
        {h.reasons.length ? <Table columns={REASON_COLUMNS} rows={h.reasons} rowKey={(r) => r.key ?? `${r.area}-${r.text}`} /> : null}
        {h.features?.length ? (
          <details className="sys-details">
            <summary>켜진 기능 {h.features.length}</summary>
            <Chips items={h.features} max={40} />
          </details>
        ) : null}
        {flags.length ? (
          <details className="sys-details">
            <summary>제어 플래그 {flags.length}</summary>
            <Chips items={flags} max={40} />
          </details>
        ) : null}
      </>
    );
  });
}
