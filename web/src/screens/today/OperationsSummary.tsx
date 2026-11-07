import { Link } from "react-router-dom";
import { Chip } from "../../components/Chip";
import { Stats } from "../../components/Stats";
import { useResource } from "../../lib/hooks";
import type { UsageResp } from "../../lib/usage";
import type { SavetickerResp } from "../system/system";
import { savetickerState } from "../system/SavetickerStatus";

type Clock = { markets: Record<string, { schedule_open: boolean }> };
type Models = { enabled: boolean; available: boolean };

function analysisState(u: UsageResp | null): { text: string; detail: string; limited: boolean } {
  if (!u?.throttle || !u?.windows?.today) return { text: "확인 필요", detail: "사용량 상태를 불러오지 못했습니다", limited: true };
  const today = u.windows.today;
  const analyze = u.caps_by_kind?.find((x) => x.kind === "analyze");
  if (today.sessions >= today.cap) return { text: "새 분석 제한", detail: "하루 세션 상한 도달", limited: true };
  if (analyze?.cap !== null && analyze?.cap !== undefined && analyze.used >= analyze.cap) return { text: "새 분석 제한", detail: "종목 분석 상한 도달", limited: true };
  if (u.throttle.state !== "ok") return { text: "새 분석 제한", detail: u.throttle.reason ?? "사용량 제한으로 새 분석을 보류합니다", limited: true };
  return { text: "호출 한도 내", detail: "실행 여부는 일정·작업 설정에 따릅니다", limited: false };
}

/** 과거 기록 화면에서도 이 절은 현재 상태임을 명시한다. 주문 가능 여부와 혼동하지 않는다. */
export function OperationsSummary({ historical = false }: { historical?: boolean }) {
  const clock = useResource<Clock>("/system/market-clock", undefined, ["daemon"]);
  const usage = useResource<UsageResp>("/system/usage", undefined, ["usage"]);
  const collection = useResource<SavetickerResp>("/system/saveticker", undefined, ["daemon"]);
  const models = useResource<Models>("/settings/codex-debate", undefined, ["control"]);
  const analysis = analysisState(usage.error ? null : usage.data);
  const state = collection.data && !collection.error ? savetickerState(collection.data) : { text: "확인 필요", tone: "warn" as const };
  const m = models.error ? null : models.data;
  return <section className="operations-summary" aria-labelledby="operations-title">
    <div className="operations-heading"><h2 id="operations-title">현재 운영 상태</h2><Link to="/system">운영 상세</Link></div>
    {historical ? <p className="operations-context">선택한 과거 날짜의 상태가 아닙니다. 아래는 현재 설정과 관측입니다.</p> : null}
    <div className="operations-grid"><Stats cols={4} items={[
      { label: "시장 시간", num: false, value: <div className="operations-markets">{["KR", "US"].map((market) => {
        const status = clock.error ? undefined : clock.data?.markets[market];
        return <span key={market}><strong>{market}</strong> <Chip tone={status?.schedule_open ? "ok" : "muted"}>{status ? status.schedule_open ? "정규장 시간" : "장외·휴장" : "확인 필요"}</Chip></span>;
      })}</div>, sub: "예정 시간 기준 · 실제 휴장 여부는 별도 확인" },
      { label: "새 분석", num: false, value: <Chip tone={analysis.limited ? "warn" : "ok"}>{analysis.text}</Chip>, sub: <><p>{analysis.detail}</p><Link to="/system#usage">사용 한도 보기</Link></> },
      { label: "SaveTicker 수집", num: false, value: <Chip tone={state.tone}>{state.text}</Chip>, sub: <><p>항목별 성공·값 변화·지연을 확인하세요</p><Link to="/system/features#saveticker">수집 내역 보기</Link></> },
      { label: "토론 참여", num: false, value: m ? m.enabled ? "Claude + Codex" : "Claude · Codex 꺼짐" : "확인 필요", sub: <><p>{m?.enabled ? m.available ? "Codex 로그인은 실행 시 확인" : "Codex 설치 필요" : "다음 새 분석에 적용되는 설정"}</p><Link to="/system/admin#settings">토론 설정</Link></> },
    ]} /></div>
  </section>;
}
