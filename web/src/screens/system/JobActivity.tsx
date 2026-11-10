// 실행 단계·요청 큐·과거 오류를 구분한다. 단계와 요청은 같은 작업을 포함할 수 있다.
import { Chip } from "../../components/Chip";
import { fmtWhen } from "../../lib/format";
import { useResource } from "../../lib/hooks";
import { body } from "../performance/ResourceView";
import { kindLabel, type ActivityResp, type JobsResp } from "./system";
import "./JobQueue.css";

export function JobActivity({ compact = false }: { compact?: boolean }) {
  const activity = useResource<ActivityResp>("/jobs/activity", undefined, ["jobs", "runs", "daemon"]);
  const jobs = useResource<JobsResp>("/jobs", undefined, ["jobs"]);
  const labels = new Map((jobs.data?.kinds ?? []).map((job) => [job.kind, job.label]));
  return (
    <section className="job-activity" aria-label="자동 작업 현황">
      <h3 className="perf-sub">자동 작업 현황</h3>
      {body(activity, "자동 작업 현황", (data) => (
        <>
          <p className="job-activity-counts">
            <Chip tone={data.running_runs.length ? "warn" : "muted"}>실행 단계 {data.running_runs.length}개</Chip>{" "}
            <span>요청 대기 {data.queued_requests}건 · 요청 실행 중 {data.running_requests}건</span>
          </p>
          <p className="data-muted">예약 작업과 하위 분석은 한 작업의 여러 단계일 수 있습니다. 실행 단계와 요청 수는 합산하지 않습니다. 요청 대기가 없어도 예약 작업은 실행될 수 있습니다.</p>
          {!compact && data.running_runs.length ? (
            <ul className="job-active-steps">
              {data.running_runs.map((run) => (
                <li key={run.id}>
                  <strong>{kindLabel(run.kind, labels)}</strong> {run.market ? <Chip>{run.market}</Chip> : null}{" "}
                  <span className="data-muted">#{run.id} · 시작 {fmtWhen(run.started_at)}</span>
                </li>
              ))}
            </ul>
          ) : null}
          <p className="job-activity-history">
            최근 24시간 오류 이력 {data.recent_errors.total}건 · 확인 필요 {data.recent_errors.failed}건 · 후속 정상 {data.recent_errors.followed_by_success}건 · 종료 요청 중단 {data.recent_errors.interrupted}건
          </p>
          {!compact ? <p className="data-muted">오류 이력은 현재 장애 건수와 다릅니다. 같은 대상 범위의 후속 정상 실행도 원인 해결을 보장하지 않습니다. 대상 범위 미확인 이력은 확인 필요로 남깁니다.</p> : null}
          <small className="data-muted">{fmtWhen(data.checked_at)} 조회 · 실행 단계는 원장 기록 기준입니다. 실제 진행 여부는 데몬·하트비트와 함께 확인하세요.</small>
        </>
      ))}
    </section>
  );
}
