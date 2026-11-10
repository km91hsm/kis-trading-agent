import { Link } from "react-router-dom";
import { useResource } from "../../lib/hooks";
import { type Health } from "../../lib/health";
import { resolveLink } from "../../lib/nav";

/** 현재 운영 점검을 다음 행동과 연결한다. 과거 날짜를 골라도 현재 점검임을 표시한다. */
export function Attention({ historical = false }: { historical?: boolean }) {
  const health = useResource<Health>("/system/health", undefined, ["daemon", "runs", "orders", "control"]);
  const reasons = health.data?.reasons.filter((r) => r.level === "warn" || r.level === "fail") ?? [];
  return <aside className="today-attention" aria-labelledby="attention-title">
    <div className="section-heading"><h2 id="attention-title">{historical ? "현재 확인할 항목" : "확인이 필요한 항목"}</h2><Link to="/system#health">운영 점검 전체 보기</Link></div>
    {health.error ? <p role="alert">운영 점검을 불러오지 못했습니다. 운영 점검 화면에서 다시 확인하세요.</p> : !health.data ? <p className="data-muted">운영 상태를 확인하는 중…</p> : reasons.length ? <ul className="attention-items">{reasons.slice(0, 3).map((r, i) => <li key={`${r.area}-${i}`}>
      <span>{r.text.split("\n")[0]}</span><Link to={r.link?.startsWith("/") && !r.link.startsWith("//") ? resolveLink(r.link) : "/system#health"}>원인 확인</Link>
    </li>)}</ul> : <p className="data-muted">{health.data.level === "ok" ? "현재 운영 점검에서 확인할 문제는 없습니다. 매수 허용 여부는 시장별 정책을 확인하세요." : "운영 점검에 주의 상태가 있습니다. 전체 보기에서 확인하세요."}</p>}
  </aside>;
}
