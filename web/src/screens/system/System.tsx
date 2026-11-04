// 「시스템」 화면 — `/system`. 사용량·헬스·데몬·잡 큐·runs/calls·설정·연결 키·쓰기 토큰·조작 이력·기능 상태(국면·저장소·단타·파생).
// 절 컨테이너와 목차는 데이터 전에 바로 그리고(메뉴 앵커 regime·saveticker), 절마다 로딩·오류를 따로 보인다.
import { useState, type ReactNode } from "react";
import { Chip } from "../../components/Chip";
import { Table, type Column } from "../../components/Table";
import { errorText, postJson } from "../../lib/api";
import { fmtTime } from "../../lib/format";
import { ControlLog } from "./ControlLog";
import { Daemon } from "./Daemon";
import { FeatureStatus } from "./FeatureStatus";
import { Health } from "./Health";
import { JobQueue } from "./JobQueue";
import { Runs } from "./Runs";
import { Secrets } from "./Secrets";
import { SettingsPanel } from "./SettingsPanel";
import { TokenPanel } from "./TokenPanel";
import { Usage } from "./Usage";
import type { FoAccount, FoResp, RegimeMarket, RegimeResp, SavetickerResp, ScalpResp } from "./system.ts";

const SECTIONS = [
  { id: "usage", label: "사용량" },
  { id: "health", label: "헬스" },
  { id: "daemon", label: "데몬" },
  { id: "jobs", label: "잡 큐" },
  { id: "runs", label: "실행 기록" },
  { id: "settings", label: "설정" },
  { id: "secrets", label: "연결 키" },
  { id: "token", label: "쓰기 토큰" },
  { id: "control-log", label: "조작 이력" },
  { id: "regime", label: "국면" },
  { id: "saveticker", label: "저장소" },
  { id: "scalp", label: "단타" },
  { id: "fo", label: "파생" },
] as const;

const REGIME_COLUMNS: Column<RegimeMarket>[] = [
  { key: "market", label: "시장", width: "20%" },
  { key: "state", label: "국면", width: "20%", align: "chip", render: (m) => <Chip>{m.state}</Chip> },
  { key: "count", label: "신호 수", width: "20%", align: "num" },
  { key: "since", label: "부터", width: "20%" },
  { key: "asof", label: "기준", width: "20%" },
];

const FO_COLUMNS: Column<FoAccount>[] = [
  { key: "id", label: "계좌", width: "25%" },
  { key: "env", label: "환경", width: "15%" },
  { key: "mode", label: "모드", width: "15%" },
  { key: "state", label: "상태", width: "20%", render: (a) => a.state ?? "—" },
  { key: "paused", label: "일시정지", width: "10%", render: (a) => (a.paused ? "예" : "아니오") },
  { key: "triggers", label: "트리거", width: "15%", align: "num", render: (a) => String(a.triggers.length) },
];

function SavetickerStatus({ s }: { s: SavetickerResp }) {
  const [note, setNote] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const restart = async () => {
    setBusy(true);
    setError(null);
    try {
      const r = await postJson<{ ok: boolean; note: string | null }>("/system/saveticker/restart", null);
      setNote(r.note || "다시 시작을 요청했습니다 — 데몬이 1분 안에 처리합니다");
    } catch (e) {
      setError(errorText(e));
    } finally {
      setBusy(false);
    }
  };
  return (
    <>
      <p>
        <Chip tone={s.blocked ? "fail" : s.health === "ok" ? "ok" : "warn"}>{s.blocked ? "막힘" : (s.service?.state ?? "알 수 없음")}</Chip>{" "}
        <span className="data-muted">
          {s.configured ? "설정됨" : "설정 안 됨"} · {s.backend} · 마지막 성공 {fmtTime(s.last_success_at)}
        </span>
      </p>
      {s.reason_text ? <p className="perf-note">{s.reason_text}</p> : null}
      <button type="button" className="btn" disabled={busy} onClick={() => void restart()}>
        다시 시작
      </button>
      {note ? <p className="perf-note">{note}</p> : null}
      {error ? (
        <p className="data-error" role="alert">
          {error}
        </p>
      ) : null}
    </>
  );
}

export function System() {
  const [tokenKey, setTokenKey] = useState(0);
  const content: Record<(typeof SECTIONS)[number]["id"], ReactNode> = {
    usage: <Usage />,
    health: <Health />,
    daemon: <Daemon />,
    jobs: <JobQueue />,
    runs: <Runs />,
    settings: <SettingsPanel />,
    secrets: <Secrets tokenKey={tokenKey} />,
    token: <TokenPanel onChange={() => setTokenKey((x) => x + 1)} />,
    "control-log": <ControlLog />,
    regime: (
      <FeatureStatus<RegimeResp> path="/system/regime" keys={["market_views", "control"]} what="국면">
        {(r) => (
          <>
            <p>
              <Chip tone={r.enabled ? "ok" : "muted"}>{r.enabled ? "헤지 켜짐" : "헤지 꺼짐"}</Chip>{" "}
              {r.killswitch.on ? <Chip tone="fail">킬스위치 {r.killswitch.reason ?? ""}</Chip> : null}{" "}
              <span className="data-muted">최대 비중 {r.max_weight_pct}%</span>
            </p>
            <Table columns={REGIME_COLUMNS} rows={r.markets} rowKey={(m) => m.market} empty="국면 기록 없음" />
          </>
        )}
      </FeatureStatus>
    ),
    saveticker: (
      <FeatureStatus<SavetickerResp> path="/system/saveticker" keys={["daemon"]} what="저장소">
        {(s) => <SavetickerStatus s={s} />}
      </FeatureStatus>
    ),
    scalp: (
      <FeatureStatus<ScalpResp> path="/system/scalp" keys={["scalp"]} what="단타">
        {(s) => (
          <>
            <p>
              <Chip tone={s.enabled ? "ok" : "muted"}>{s.enabled ? "켜짐" : "꺼짐"}</Chip>{" "}
              <span className="data-muted">
                판정자 {s.judge ? "켜짐" : "꺼짐"} · 최대 비중 {s.max_weight_pct}% · 최대 {s.max_positions}종목
              </span>
            </p>
            <ul className="sys-lines">
              {Object.entries(s.markets).map(([m, v]) => (
                <li key={m}>
                  {m} · 보유 {v.open.length} · 청산 {v.closed.length}
                  {v.halted ? " · 중단" : ""}
                </li>
              ))}
            </ul>
          </>
        )}
      </FeatureStatus>
    ),
    fo: (
      <FeatureStatus<FoResp> path="/system/fo" keys={["fo", "triggers"]} what="파생">
        {(f) => (
          <>
            {f.context ? (
              <p className="data-muted">
                국면 {f.context.regime?.state ?? "—"} · 시장 판단 {f.context.market_view?.stance ?? "—"}
              </p>
            ) : null}
            <Table columns={FO_COLUMNS} rows={f.accounts} rowKey={(a) => a.id} empty="파생 계좌 없음" />
          </>
        )}
      </FeatureStatus>
    ),
  };

  return (
    <div className="screen" data-screen="system">
      <h1 className="screen-title">시스템</h1>
      <nav aria-label="목차" className="sys-toc">
        {SECTIONS.map((s) => (
          <a key={s.id} href={`#${s.id}`}>
            {s.label}
          </a>
        ))}
      </nav>
      {SECTIONS.map((s) => (
        <section key={s.id} id={s.id} className="screen-section">
          <h2>{s.label}</h2>
          {content[s.id]}
        </section>
      ))}
    </div>
  );
}
