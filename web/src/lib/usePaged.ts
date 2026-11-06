// 긴 목록을 30행씩 자른다. 현재 페이지는 주소 `?page=N`(1 이면 지움) — 새로고침·뒤로가기에도 남는다.
// 행 수가 줄어 범위를 벗어나면 마지막 페이지로 죈다.
import { useCallback, useMemo } from "react";
import { useSearchParams } from "react-router-dom";

export type Paged<T> = { rows: T[]; page: number; pages: number; total: number; from: number; to: number; setPage: (n: number) => void };

export const PAGE_SIZE = 30;

export function usePaged<T>(all: T[], size = PAGE_SIZE, param = "page"): Paged<T> {
  const [sp, setSp] = useSearchParams();
  const total = all.length;
  const pages = Math.max(1, Math.ceil(total / size));
  const raw = Number.parseInt(sp.get(param) ?? "1", 10);
  const page = Math.min(pages, Math.max(1, Number.isFinite(raw) ? raw : 1));
  const rows = useMemo(() => all.slice((page - 1) * size, page * size), [all, page, size]);
  const setPage = useCallback(
    (n: number) => {
      const next = new URLSearchParams(sp);
      if (n <= 1) next.delete(param);
      else next.set(param, String(n));
      setSp(next);
    },
    [sp, setSp, param],
  );
  return { rows, page, pages, total, from: total ? (page - 1) * size + 1 : 0, to: Math.min(total, page * size), setPage };
}

/** 필터(날짜·종류)가 바뀌어 목록이 새로 올 때 1쪽으로 — `?<param>` 을 지운다. */
export function usePageReset(param = "page"): () => void {
  const [sp, setSp] = useSearchParams();
  return useCallback(() => {
    if (!sp.has(param)) return;
    const next = new URLSearchParams(sp);
    next.delete(param);
    setSp(next, { replace: true });
  }, [sp, setSp, param]);
}
