// 기능 상태 절 공통 — 기능 라우트를 읽어 그리고, 기능이 없으면(404) 「기능 없음」, 그 밖 오류·로딩은 성과 화면과 같은 모양.
import type { ReactNode } from "react";
import { Empty } from "../../components/Empty";
import type { SseKey } from "../../generated/sse-keys";
import { useResource, type Resource } from "../../lib/hooks";
import { body } from "../performance/ResourceView";

export function FeatureStatus<T>({
  path,
  keys,
  what,
  children,
}: {
  path: string;
  keys: SseKey[];
  what: string;
  children: (data: T, res: Resource<T>) => ReactNode;
}) {
  const res = useResource<T>(path, undefined, keys);
  if (res.error?.status === 404) return <Empty title="기능 없음">이 설치에는 {what} 기능이 없습니다.</Empty>;
  return body(res, what, (d) => children(d, res));
}
