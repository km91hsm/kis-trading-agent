// 페이지 번호 줄 「‹ 1 2 3 ›  31–60 / 65」 — usePaged 의 결과를 그대로 받는다. 한 페이지뿐이면 아무것도 그리지 않는다.
// 번호가 많으면 첫·끝과 현재 주변만(… 으로 줄임).
import type { Paged } from "../lib/usePaged";
import { useLayoutEffect, useRef } from "react";

function numbers(page: number, pages: number): (number | "…")[] {
  if (pages <= 7) return Array.from({ length: pages }, (_, i) => i + 1);
  const near = [page - 1, page, page + 1].filter((n) => n > 1 && n < pages);
  const out: (number | "…")[] = [1];
  if (near[0] > 2) out.push("…");
  out.push(...near);
  if (near[near.length - 1] < pages - 1) out.push("…");
  out.push(pages);
  return out;
}

export function Pager({ page, pages, total, from, to, setPage }: Omit<Paged<unknown>, "rows">) {
  const nav = useRef<HTMLElement>(null);
  const pending = useRef(false);
  useLayoutEffect(() => {
    if (!pending.current || !nav.current) return;
    pending.current = false;
    // 같은 화면의 다른 표를 건드리지 않고 이 번호 줄 앞의 목록으로 돌아간다.
    let previous = nav.current.previousElementSibling;
    while (previous && !previous.matches(".table-block, [data-pagination-target], table, ul, ol")) previous = previous.previousElementSibling;
    const target = previous instanceof HTMLElement ? previous : nav.current.closest<HTMLElement>("[data-pagination-region], section, main");
    if (!target) return;
    if (!target.hasAttribute("tabindex")) target.tabIndex = -1;
    target.focus({ preventScroll: true });
    target.scrollIntoView?.({ block: "start", behavior: "instant" });
  }, [page]);
  const changePage = (next: number) => {
    if (next === page || next < 1 || next > pages) return;
    pending.current = true;
    setPage(next);
  };
  if (pages <= 1) return null;
  return (
    <nav ref={nav} className="pager" aria-label="페이지">
      <button type="button" className="pager-btn" data-prev disabled={page <= 1} onClick={() => changePage(page - 1)} aria-label="이전 페이지">
        ‹
      </button>
      {numbers(page, pages).map((n, i) =>
        n === "…" ? (
          <span key={`e${i}`} className="pager-gap" aria-hidden="true">
            …
          </span>
        ) : (
          <button key={n} type="button" className="pager-btn num" data-page={n} aria-current={n === page ? "page" : undefined} onClick={() => changePage(n)}>
            {n}
          </button>
        ),
      )}
      <button type="button" className="pager-btn" data-next disabled={page >= pages} onClick={() => changePage(page + 1)} aria-label="다음 페이지">
        ›
      </button>
      <span className="pager-range num">
        {from}–{to} / {total}
      </span>
    </nav>
  );
}
