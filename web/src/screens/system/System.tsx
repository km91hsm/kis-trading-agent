// 「시스템」 화면 — `/system`. 절 순서는 generated nav.ts 의 system 항목과 같다: 지금 볼 것(헬스·데몬·잡 큐·실행 기록·사용량)
// → 기능 상태(국면·저장소·단타·파생) → 관리(설정·연결 키·쓰기 토큰·조작 이력).
// 절 컨테이너는 데이터 전에 바로 그리고(절 id = nav.ts 항목 — 왼쪽 메뉴가 목차), 절마다 로딩·오류를 따로 보인다.
import { useState, type ReactNode } from "react";
import { Chip } from "../../components/Chip";
import { Confirm } from "../../components/Confirm";
import { Stats } from "../../components/Stats";
import { Table, type Column } from "../../components/Table";
import { errorText, postJson } from "../../lib/api";
import { fmtPct, fmtWhen } from "../../lib/format";
import { termLabel } from "../../lib/glossary";
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
  { id: "health", label: "헬스" },
  { id: "daemon", label: "데몬" },
  { id: "jobs", label: "잡 큐" },
  { id: "runs", label: "실행 기록" },
  { id: "usage", label: "사용량" },
  { id: "regime", label: "국면" },
  { id: "saveticker", label: "저장소" },
  { id: "scalp", label: "단타" },
  { id: "fo", label: "파생" },
  { id: "settings", label: "설정" },
  { id: "secrets", label: "연결 키" },
  { id: "token", label: "쓰기 토큰" },
  { id: "control-log", label: "조작 이력" },
] as const;

const REGIME_TONE: Record<string, "ok" | "warn" | "muted"> = { bull: "ok", bear: "warn", neutral: "muted" };
const regimeChip = (state: string | null | undefined) =>
  state ? <Chip tone={REGIME_TONE[state] ?? "muted"}>{termLabel(`regime.${state}`)}</Chip> : <Chip>자료 없음</Chip>;

const REGIME_COLUMNS: Column<RegimeMarket>[] = [
  { key: "market", label: "시장", width: "20%" },
  { key: "state", label: "국면", width: "20%", align: "chip", render: (m) => regimeChip(m.state) },
  { key: "count", label: "신호 수", width: "20%", align: "num", render: (m) => (m.state ? String(m.count) : "—") },
  { key: "since", label: "부터", width: "20%", align: "num", render: (m) => fmtWhen(m.since) },
  { key: "asof", label: "기준", width: "20%", align: "num", render: (m) => fmtWhen(m.asof) },
];

const FO_COLUMNS: Column<FoAccount>[] = [
  { key: "id", label: "계좌", width: "25%", long: true },
  { key: "env", label: "환경", width: "15%", align: "chip", render: (a) => <Chip tone={a.env === "real" ? "live" : "muted"}>{termLabel(`env.${a.env}`)}</Chip> },
  { key: "mode", label: "모드", width: "15%", render: (a) => termLabel(`mode.${a.mode}`) },
  { key: "state", label: "상태", width: "20%", render: (a) => (a.state ? termLabel(`fo.status.${a.state}`) : "—") },
  { key: "paused", label: "일시정지", width: "10%", render: (a) => (a.paused ? <Chip tone="warn">일시정지</Chip> : "—") },
  { key: "triggers", label: "트리거", width: "15%", align: "num", render: (a) => String(a.triggers.length) },
];

type ScalpMarketRow = { market: string; open: number; closed: number; halted: boolean };
const SCALP_COLUMNS: Column<ScalpMarketRow>[] = [
  { key: "market", label: "시장", width: "25%" },
  { key: "open", label: "보유", width: "25%", align: "num" },
  { key: "closed", label: "청산", width: "25%", align: "num" },
  { key: "halted", label: "상태", width: "25%", align: "chip", render: (m) => <Chip tone={m.halted ? "warn" : "ok"}>{m.halted ? "중단" : "정상"}</Chip> },
];

const SERVICE_STATE: Record<string, { text: string; tone: "ok" | "warn" | "fail" | "muted" }> = {
  running: { text: "실행 중", tone: "ok" },
  stopped: { text: "멈춤", tone: "warn" },
  failed: { text: "실패", tone: "fail" },
};

function savetickerState(s: SavetickerResp): { text: string; tone: "ok" | "warn" | "fail" | "muted" } {
  if (s.blocked) return { text: "막힘", tone: "fail" };
  if (!s.configured) return { text: "설정 안 됨", tone: "muted" };
  const st = s.service?.state;
  if (st && SERVICE_STATE[st]) return SERVICE_STATE[st];
  if (st) return { text: st, tone: s.health === "ok" ? "ok" : "warn" };
  return { text: "상태 없음", tone: "warn" };
}

function SavetickerStatus({ s }: { s: SavetickerResp }) {
  const [note, setNote] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [confirming, setConfirming] = useState(false);
  const restart = async () => {
    setConfirming(false);
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
  const state = savetickerState(s);
  return (
    <>
      <Stats
        cols={4}
        items={[
          { label: "상태", value: <Chip tone={state.tone}>{state.text}</Chip>, num: false },
          { label: "설정", value: s.configured ? "설정됨" : "설정 안 됨", num: false },
          { label: "백엔드", value: s.backend, num: false },
          { label: "마지막 성공", value: fmtWhen(s.last_success_at) },
        ]}
      />
      {s.reason_text ? <p className="perf-note">{s.reason_text}</p> : null}
      <button type="button" className="btn" disabled={busy || !s.configured} onClick={() => setConfirming(true)}>
        다시 시작
      </button>
      {note ? <p className="perf-note">{note}</p> : null}
      {error ? (
        <p className="data-error" role="alert">
          {error}
        </p>
      ) : null}
      <Confirm
        open={confirming}
        title="저장소 수집 다시 시작"
        body={<p>수집 서비스를 다시 시작하도록 데몬에 요청합니다. 쌓인 데이터는 그대로입니다.</p>}
        onConfirm={() => void restart()}
        onClose={() => setConfirming(false)}
      />
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
            <p className="sys-head">
              <Chip tone={r.enabled ? "ok" : "muted"}>{r.enabled ? "헤지 켜짐" : "헤지 꺼짐"}</Chip>
              {r.killswitch.on ? <Chip tone="fail">킬스위치 {r.killswitch.reason ?? ""}</Chip> : null}
              <span className="data-muted">최대 비중 {fmtPct(r.max_weight_pct, 0)}</span>
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
            <Stats
              cols={4}
              items={[
                { label: "상태", value: <Chip tone={s.enabled ? "ok" : "muted"}>{s.enabled ? "켜짐" : "꺼짐"}</Chip>, num: false },
                { label: "판정자", value: s.judge ? "켜짐" : "꺼짐", num: false },
                { label: "최대 비중", value: fmtPct(s.max_weight_pct, 0) },
                { label: "최대 종목", value: `${s.max_positions}종목` },
              ]}
            />
            <Table
              columns={SCALP_COLUMNS}
              rows={Object.entries(s.markets).map(([market, v]) => ({ market, open: v.open.length, closed: v.closed.length, halted: v.halted }))}
              rowKey={(m) => m.market}
              empty="운영 중인 시장 없음"
            />
          </>
        )}
      </FeatureStatus>
    ),
    fo: (
      <FeatureStatus<FoResp> path="/system/fo" keys={["fo", "triggers"]} what="파생">
        {(f) => (
          <>
            {f.context ? (
              <p className="sys-head">
                <span className="data-muted">국면</span> {regimeChip(f.context.regime?.state)}
                <span className="data-muted">시장 판단</span>{" "}
                {f.context.market_view?.stance ? <Chip>{termLabel(`view.${f.context.market_view.stance}`)}</Chip> : <Chip>자료 없음</Chip>}
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
      {SECTIONS.map((s) => (
        <section key={s.id} id={s.id} className="screen-section">
          <h2>{s.label}</h2>
          {content[s.id]}
        </section>
      ))}
    </div>
  );
}
