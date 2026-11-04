// 2단 메뉴 — 화면 5개(1단)와 화면 안 섹션 목차(2단, 자동 생성 NAV). 폭에 따라 shell.css 가
// 왼쪽 메뉴(≥1280)·아이콘 레일(1024~1279)·하단 탭(<1024)으로 바꾼다. 하위 목차는 `#<id>` 앵커.
import { useEffect, useState } from "react";
import { Link, useLocation } from "react-router-dom";
import { Icon } from "../../components/Icon";
import { NAV } from "../../generated/nav";

export type Screen = keyof typeof NAV;
export type NavGroup = { screen: Screen; label: string; path: string; items: { id: string; label: string; order: number }[] };

const SCREENS = Object.keys(NAV) as Screen[];

/** NAV 를 화면 순서대로, 각 화면의 items 를 order 오름차순으로. */
export function navGroups(): NavGroup[] {
  return SCREENS.map((screen) => {
    const s = NAV[screen];
    const items = [...s.items].sort((a, b) => a.order - b.order).map(({ id, label, order }) => ({ id, label, order }));
    return { screen, label: s.label, path: s.path, items };
  });
}

/** 경로가 속한 화면. `/accounts/paper-1` → accounts, `/decisions/...` 는 어느 화면도 아니다. */
export function screenOf(pathname: string): Screen | null {
  if (pathname === "/") return "today";
  return SCREENS.find((s) => s !== "today" && (pathname === NAV[s].path || pathname.startsWith(NAV[s].path + "/"))) ?? null;
}

/** 지금 보이는 섹션 id — IntersectionObserver 가 있으면 화면에 들어온 가장 위 섹션, 없으면 location.hash. */
function useCurrentSection(ids: string[], hash: string): string | null {
  const [seen, setSeen] = useState<string | null>(null);
  const key = ids.join(",");
  useEffect(() => {
    setSeen(null);
    if (typeof IntersectionObserver !== "function" || !key) return;
    const visible = new Set<string>();
    const order = key.split(",");
    const io = new IntersectionObserver((entries) => {
      for (const e of entries) {
        if (e.isIntersecting) visible.add(e.target.id);
        else visible.delete(e.target.id);
      }
      setSeen(order.find((id) => visible.has(id)) ?? null);
    });
    for (const id of order) {
      const el = document.getElementById(id);
      if (el) io.observe(el);
    }
    return () => io.disconnect();
  }, [key]);
  const fromHash = hash.startsWith("#") ? decodeURIComponent(hash.slice(1)) : "";
  return seen ?? (ids.includes(fromHash) ? fromHash : null);
}

export function Nav() {
  const { pathname, hash } = useLocation();
  const groups = navGroups();
  const active = screenOf(pathname);
  const activeIds = groups.find((g) => g.screen === active)?.items.map((i) => i.id) ?? [];
  const current = useCurrentSection(activeIds, hash);

  return (
    <nav className="nav" aria-label="주 메뉴">
      <ul className="nav-groups">
        {groups.map((g) => {
          const on = g.screen === active;
          return (
            <li key={g.screen} className="nav-group" data-active={on || undefined}>
              <Link to={g.path} className="nav-screen" data-nav-screen={g.screen} aria-current={on ? "page" : undefined} title={g.label}>
                <Icon name={g.screen} />
                <span className="nav-label">{g.label}</span>
              </Link>
              <ul className="nav-items">
                {g.items.map((it) => (
                  <li key={it.id}>
                    <Link
                      to={{ pathname: g.path, hash: `#${it.id}` }}
                      className="nav-item"
                      data-nav-item={it.id}
                      aria-current={on && current === it.id ? "location" : undefined}
                    >
                      {it.label}
                    </Link>
                  </li>
                ))}
              </ul>
            </li>
          );
        })}
      </ul>
    </nav>
  );
}
