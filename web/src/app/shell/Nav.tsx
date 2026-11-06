// 2단 메뉴 — 화면 5개(1단)와 현재 화면의 2단(자동 생성 NAV). 폭에 따라 shell.css 가
// 왼쪽 메뉴(≥1280)·아이콘 레일(1024~1279)·하단 탭(<1024)으로 바꾼다.
// 2단은 화면에 페이지가 여럿이면(시스템: 상태·잡·기능·관리) 페이지 링크, 하나면 절 `#<id>` 앵커 목차.
// 하위 목차는 현재 화면 것만 펼치고, 화면이 `hidden` 으로 숨긴 섹션(승인 0건·없는 레인 등)은 목차에서도 뺀다.
// 현재 항목: 방금 누른 항목(주소 해시)이 우선, 사용자가 스크롤 입력을 하면 보이는 가장 위 섹션(IntersectionObserver).
import { useEffect, useState } from "react";
import { Link, useLocation } from "react-router-dom";
import { Icon } from "../../components/Icon";
import { NAV } from "../../generated/nav";
import { type Health, healthDot } from "../../lib/health";
import { useResource } from "../../lib/hooks";
import { screenPages } from "../../lib/nav";

export type Screen = keyof typeof NAV;
export type NavGroup = { screen: Screen; label: string; path: string; items: { id: string; label: string; order: number }[] };

const SCREENS = Object.keys(NAV) as Screen[];
const SCROLL_KEYS = new Set(["ArrowUp", "ArrowDown", "PageUp", "PageDown", "Home", "End", " "]);
const LEVEL_TEXT = { ok: "정상", warn: "주의", fail: "장애" } as const;

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

/** 본문에 실제로 있고 `hidden` 이 아닌 섹션 id 만. 본문 DOM 의 hidden 변화·교체를 MutationObserver 로 따라간다. */
function useShownSections(ids: string[], pathname: string): string[] {
  const key = ids.join(",");
  const [state, setState] = useState({ key, shown: key });
  useEffect(() => {
    const compute = () => {
      const shown = key
        ? key
            .split(",")
            .filter((id) => {
              const el = document.getElementById(id);
              return !!el && !el.hidden;
            })
            .join(",")
        : "";
      setState((s) => (s.key === key && s.shown === shown ? s : { key, shown }));
    };
    compute();
    if (typeof MutationObserver !== "function") return;
    const mo = new MutationObserver(compute);
    mo.observe(document.querySelector("main") ?? document.body, { subtree: true, childList: true, attributes: true, attributeFilter: ["hidden"] });
    return () => mo.disconnect();
  }, [key, pathname]);
  const shown = state.key === key ? state.shown : key;
  return shown ? shown.split(",") : [];
}

/** 지금 표시할 섹션 id — 방금 누른 항목(해시, 이동마다 새 key)이 우선이고, 스크롤 입력이 오면
 *  IntersectionObserver 가 고른 보이는 가장 위 섹션으로 넘어간다. IO 가 없으면 해시. */
function useCurrentSection(ids: string[], hash: string, key: string): string | null {
  const [seen, setSeen] = useState<string | null>(null);
  const [clearedKey, setClearedKey] = useState<string | null>(null);
  const idsKey = ids.join(",");
  useEffect(() => {
    setSeen(null);
    if (typeof IntersectionObserver !== "function" || !idsKey) return;
    const visible = new Set<string>();
    const order = idsKey.split(",");
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
  }, [idsKey]);

  const fromHash = hash.startsWith("#") ? decodeURIComponent(hash.slice(1)) : "";
  const hashId = ids.includes(fromHash) ? fromHash : null;
  const picked = clearedKey !== key ? hashId : null;
  useEffect(() => {
    if (!picked) return;
    const clear = (e: Event) => {
      if (e.type === "keydown" && !SCROLL_KEYS.has((e as KeyboardEvent).key)) return;
      setClearedKey(key);
    };
    const opts: AddEventListenerOptions = { passive: true };
    window.addEventListener("wheel", clear, opts);
    window.addEventListener("touchmove", clear, opts);
    window.addEventListener("keydown", clear, opts);
    return () => {
      window.removeEventListener("wheel", clear);
      window.removeEventListener("touchmove", clear);
      window.removeEventListener("keydown", clear);
    };
  }, [picked, key]);
  return picked ?? seen ?? hashId;
}

/** 메뉴 푸터 「데몬 정상 · v0.2.0」 — 상태 띠와 같은 /system/health(진행 중 요청은 나눠 쓴다). */
function NavFoot() {
  const { data: h, error } = useResource<Health>("/system/health", undefined, ["daemon", "runs", "usage", "control"]);
  const level = h ? LEVEL_TEXT[healthDot(h.level)] : error ? "확인 실패" : "확인 중";
  return (
    <div className="nav-foot" data-nav-foot>
      <div>
        데몬 {level}
        {h?.version ? <span className="num"> · v{h.version}</span> : null}
      </div>
    </div>
  );
}

export function Nav() {
  const { pathname, hash, key } = useLocation();
  const groups = navGroups();
  const active = screenOf(pathname);
  const pages = active ? screenPages(active) : [];
  const paged = pages.length > 1; // 페이지가 여럿인 화면은 2단이 페이지 링크 — 절 목차·IO 는 쓰지 않는다
  const activeIds = paged ? [] : (groups.find((g) => g.screen === active)?.items.map((i) => i.id) ?? []);
  const shown = useShownSections(activeIds, pathname);
  const current = useCurrentSection(activeIds, hash, key);

  return (
    <nav className="nav" aria-label="주 메뉴">
      <div className="nav-brand" aria-hidden="true">
        trader
      </div>
      <ul className="nav-groups">
        {groups.map((g) => {
          const on = g.screen === active;
          return (
            <li key={g.screen} className="nav-group" data-active={on || undefined}>
              <Link to={g.path} className="nav-screen" data-nav-screen={g.screen} aria-current={on ? "page" : undefined} title={g.label}>
                <Icon name={g.screen} />
                <span className="nav-label">{g.label}</span>
              </Link>
              {on && paged ? (
                <ul className="nav-items">
                  {pages.map((p) => (
                    <li key={p.id}>
                      <Link to={p.path} className="nav-item" data-nav-item={p.id} aria-current={pathname === p.path ? "page" : undefined}>
                        {p.label}
                      </Link>
                    </li>
                  ))}
                </ul>
              ) : on ? (
                <ul className="nav-items">
                  {g.items
                    .filter((it) => shown.includes(it.id))
                    .map((it) => (
                      <li key={it.id}>
                        <Link
                          to={{ pathname: g.path, hash: `#${it.id}` }}
                          className="nav-item"
                          data-nav-item={it.id}
                          aria-current={current === it.id ? "location" : undefined}
                        >
                          {it.label}
                        </Link>
                      </li>
                    ))}
                </ul>
              ) : null}
            </li>
          );
        })}
      </ul>
      <NavFoot />
    </nav>
  );
}
