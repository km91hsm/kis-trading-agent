// 「시스템」 화면 — 페이지 4개(generated nav.ts 의 system.pages): 상태 `/system`(헬스·데몬·사용량) · 잡 `/system/jobs`(잡 큐·실행 기록)
// · 기능 `/system/features`(국면·데이터 수집·단타·파생) · 관리 `/system/admin`(설정·연결 키·쓰기 토큰·조작 이력).
// 절이 어느 페이지에 가는지는 nav.ts 항목의 `page` 가 정한다. 절 컨테이너는 데이터 전에 바로 그리고(절 id = nav.ts 항목),
// 절마다 로딩·오류를 따로 보인다. 왼쪽 메뉴(≥1280)가 페이지 링크를 맡고, 그보다 좁으면 화면 위 `.page-tabs` 가 맡는다.
import { useState, type ReactNode } from "react";
import { Link } from "react-router-dom";
import { PageTabs } from "../../components/PageTabs";
import { FO_STATE_LABEL } from "../accounts/accounts";
import { NAV } from "../../generated/nav";
import { screenPages } from "../../lib/nav";
import { Chip } from "../../components/Chip";
import { Stats } from "../../components/Stats";
import { Table, type Column } from "../../components/Table";
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
import { SavetickerStatus } from "./SavetickerStatus";
import { RiskPolicy } from "./RiskPolicy.tsx";
import { ScalpSwitch } from "./ScalpSwitch";
import { FoSwitch } from "./FoSwitch";
import "../today/user-flow.css";
import type { FoAccount, FoResp, RegimeMarket, RegimeResp, SavetickerResp, ScalpResp } from "./system.ts";

const SECTIONS = [
  { id: "health", label: "헬스" },
  { id: "daemon", label: "데몬" },
  { id: "jobs", label: "잡 큐" },
  { id: "runs", label: "실행 기록" },
  { id: "usage", label: "사용량" },
  { id: "regime", label: "국면" },
  { id: "saveticker", label: "데이터 수집" },
  { id: "scalp", label: "단타" },
  { id: "fo", label: "파생" },
  { id: "settings", label: "설정" },
  { id: "secrets", label: "연결 키" },
  { id: "token", label: "쓰기 토큰" },
  { id: "control-log", label: "조작 이력" },
] as const;

export type SystemPage = (typeof NAV.system.pages)[number]["id"];
export const SYSTEM_PAGES = NAV.system.pages.map((p) => p.id) as SystemPage[];

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
  { key: "id", label: "계좌·보유 상세", width: "25%", long: true, render: (a) => <Link to={`/accounts/${encodeURIComponent(a.id)}`}>{a.id} · 상세 보기</Link> },
  { key: "env", label: "환경", width: "15%", align: "chip", render: (a) => <Chip tone={a.env === "real" ? "live" : "muted"}>{termLabel(`env.${a.env}`)}</Chip> },
  { key: "mode", label: "모드", width: "15%", render: (a) => termLabel(`mode.${a.mode}`) },
  { key: "state", label: "상태", width: "20%", render: (a) => (a.state ? (FO_STATE_LABEL[a.state] ?? a.state) : "—") },
  { key: "paused", label: "일시정지", width: "10%", render: (a) => (a.paused ? <Chip tone="warn">일시정지</Chip> : "—") },
  { key: "triggers", label: "트리거", width: "15%", align: "num", render: (a) => String(a.triggers.length) },
];

type ScalpMarketRow = { market: string; open: number; closed: number; halted: boolean; enabled: boolean };
const scalpChip = (m: ScalpMarketRow) =>
  !m.enabled ? <Chip tone="muted">꺼짐</Chip> : m.halted ? <Chip tone="warn">중단</Chip> : <Chip tone="ok">정상</Chip>;
const SCALP_COLUMNS: Column<ScalpMarketRow>[] = [
  { key: "market", label: "시장", width: "25%" },
  { key: "open", label: "보유", width: "25%", align: "num" },
  { key: "closed", label: "청산", width: "25%", align: "num" },
  { key: "halted", label: "상태", width: "25%", align: "chip", render: scalpChip },
];

export function System({ page }: { page: SystemPage }) {
  const pages = screenPages("system");
  const label = pages.find((p) => p.id === page)?.label ?? "";
  // 절 순서는 nav.ts 항목 순(order) — 페이지 안에서도 메뉴 생성기가 정한 순서를 따른다
  const sections = NAV.system.items.filter((i) => i.page === page).flatMap((i) => SECTIONS.filter((s) => s.id === i.id).map((s) => ({ ...s, label: i.label })));
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
    regime: (<>
      <RiskPolicy />
      <details className="sys-details"><summary>자동 헤지 설정과 시장 상태 기록</summary>
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
      </details>
    </>),
    saveticker: (
      <FeatureStatus<SavetickerResp> path="/system/saveticker" keys={["daemon"]} what="데이터 수집">
        {(s) => <SavetickerStatus s={s} />}
      </FeatureStatus>
    ),
    scalp: (
      <FeatureStatus<ScalpResp> path="/system/scalp" keys={["scalp"]} what="단타">
        {(s) => (
          <>
            <ScalpSwitch />
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
              rows={Object.entries(s.markets).map(([market, v]) => ({ market, open: v.open.length, closed: v.closed.length, halted: v.halted, enabled: s.enabled }))}
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
            <FoSwitch />
            {f.context ? (
              <p className="sys-head">
                <span className="data-muted">KR 국면</span> {regimeChip(f.context.regime?.state)}
                <span className="data-muted">시장 판단</span>{" "}
                {f.context.market_view?.stance ? <Chip>{termLabel(`view.${f.context.market_view.stance}`)}</Chip> : <Chip>자료 없음</Chip>}
              </p>
            ) : null}
            <Table columns={FO_COLUMNS} rows={f.accounts} rowKey={(a) => a.id} empty="파생 계좌 없음" />
            <p className="perf-note">보유·증거금·만기와 주문 상세는 위 계좌를 선택해 확인하세요.</p>
          </>
        )}
      </FeatureStatus>
    ),
  };

  return (
    <div className="screen" data-screen="system" data-page={page}>
      <h1 className="screen-title">설정·운영 · {label}</h1>
      <p className="system-intro">{({ status: "자동 실행과 연결 상태를 확인하고, 문제가 있으면 원인과 해결 경로를 찾습니다.", jobs: "예약된 자동 작업과 실행 결과를 확인합니다. 수동 실행은 작업별 영향을 확인한 뒤 요청하세요.", features: "시장별 일반 현물 매수 정책과 단타·파생의 실제 적용 상태를 확인하고 설정합니다.", admin: "데이터 수집·연결 키·접속 설정을 관리합니다. 매매 허용 범위는 매매 정책에서 확인하세요." })[page]}</p>
      <PageTabs screen="system" label="시스템 페이지" />
      {page === "features" ? <nav className="system-shortcuts" aria-label="매매 정책 구분"><Link to="#regime">현물 매수 정책</Link><Link to="#scalp">단타</Link><Link to="#fo">파생</Link></nav> : null}
      <nav className="system-shortcuts" aria-label="운영 바로가기"><Link to="/">오늘 상태 보기</Link><Link to="/system/features#regime">시장별 매수 정책</Link><Link to="/system/admin#saveticker">연결·수집 점검</Link></nav>
      {sections.map((s) => (
        <section key={s.id} id={s.id} className="screen-section">
          <h2>{s.label}</h2>
          {content[s.id]}
        </section>
      ))}
    </div>
  );
}
