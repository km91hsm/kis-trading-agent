// 페이지 탭 — 페이지가 여럿인 화면(시스템·오늘)의 화면 위 세그먼트. ≥1280 은 왼쪽 메뉴가 페이지 링크를 맡아 CSS 가 숨긴다.
// `search` 를 주면(오늘의 `?date=`) 페이지를 옮겨도 그 상태를 가져간다.
import { Link, useLocation } from "react-router-dom";
import { type Screen, screenPages } from "../lib/nav";

export function PageTabs({ screen, label, search = "" }: { screen: Screen; label: string; search?: string }) {
  const { pathname } = useLocation();
  return (
    <nav className="page-tabs" aria-label={label}>
      {screenPages(screen).map((p) => (
        <Link key={p.id} to={{ pathname: p.path, search }} aria-current={pathname === p.path ? "page" : undefined}>
          {p.label}
        </Link>
      ))}
    </nav>
  );
}
