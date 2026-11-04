// 다음 할 일 — 데몬 작업 중 다음 실행 시각이 가까운 3개. 데몬이 꺼져 있으면 한 줄.
import { Empty } from "../../components/Empty";
import type { ApiError } from "../../lib/api";
import { nextJobs, type Daemon } from "./today.ts";

export function NextJobs({ data, error }: { data: Daemon | null; error: ApiError | null }) {
  if (error) return <Empty title="데몬 상태를 불러오지 못했습니다">{error.message}</Empty>;
  if (!data) return <Empty title="불러오는 중" />;
  if (!data.alive) return <Empty title="데몬 꺼짐" />;
  const jobs = nextJobs(data);
  if (!jobs.length) return <Empty title="예정된 작업이 없습니다" />;
  return (
    <ol className="jobs">
      {jobs.map((j) => (
        <li key={j.id} className="job-row">
          {j.text}
        </li>
      ))}
    </ol>
  );
}
