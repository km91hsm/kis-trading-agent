// 성과 화면 절 본문 — 자원 하나의 로딩·오류(삼키지 않고 Empty 로)·데이터를 한 모양으로 그린다.
import type { ReactNode } from "react";
import { Empty } from "../../components/Empty";
import { errorText } from "../../lib/api";
import type { Resource } from "../../lib/hooks";

export function failed(what: string, e: unknown) {
  return <Empty title={`${what}을(를) 불러오지 못했습니다`}>{errorText(e)}</Empty>;
}

export function body<T>(res: Resource<T>, what: string, render: (data: T) => ReactNode): ReactNode {
  if (res.error) return failed(what, res.error);
  if (!res.data) return <Empty title="불러오는 중" />;
  return render(res.data);
}
