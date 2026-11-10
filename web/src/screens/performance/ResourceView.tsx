// 성과 화면 절 본문 — 자원 하나의 로딩·오류(삼키지 않고 Empty 로)·데이터를 한 모양으로 그린다.
import type { ReactNode } from "react";
import { Empty } from "../../components/Empty";
import { errorText } from "../../lib/api";
import type { Resource } from "../../lib/hooks";

export function failed(what: string, e: unknown, onRetry?: () => void, retrying = false) {
  const collection = /뉴스|공시|수집|시세|매크로|순위/.test(what);
  return <Empty title={`${what} 조회에 실패했습니다`} onRetry={onRetry} retrying={retrying}
    helpHref={collection ? "/system/admin#saveticker" : "/system#health"}
    helpLabel={collection ? "수집·연결 상태 확인" : "운영 상태 확인"}>{errorText(e)}</Empty>;
}

export function body<T>(res: Resource<T>, what: string, render: (data: T) => ReactNode): ReactNode {
  if (res.error) return failed(what, res.error, res.reload, res.loading);
  if (!res.data) return <Empty title="불러오는 중" />;
  return render(res.data);
}
