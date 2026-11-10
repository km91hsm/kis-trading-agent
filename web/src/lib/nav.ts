// 메뉴 페이지 — 생성 파일 nav.ts 의 `pages`(시스템은 상태·잡·기능·관리 넷, 다른 화면은 자기 자신 하나)를 읽는 헬퍼.
// 절 id 로 그 절이 있는 페이지 경로를 찾고, 백엔드가 주는 `/system#절` 링크를 페이지 주소로 바꾼다.
import { NAV } from "../generated/nav";

export type Screen = keyof typeof NAV;
export type NavPage = { id: string; label: string; path: string };

export function screenPages(screen: Screen): NavPage[] {
  return NAV[screen].pages.map((p) => ({ id: p.id, label: p.label, path: p.path }));
}

/** 절 id → 그 절이 있는 페이지 경로(모르는 절이면 null). */
export function pagePathOf(screen: Screen, sectionId: string): string | null {
  const item = (NAV[screen].items as readonly { id: string; page: string }[]).find((i) => i.id === sectionId);
  if (!item) return null;
  return screenPages(screen).find((p) => p.id === item.page)?.path ?? null;
}

/** `/system#runs` → `/system/jobs#runs`. 절이 없거나 다른 화면이면 그대로. */
export function resolveLink(link: string): string {
  const m = /^(\/[^#?]*)#([^#]+)$/.exec(link);
  if (!m) return link;
  const screen = (Object.keys(NAV) as Screen[]).find((s) => NAV[s].path === m[1] || screenPages(s).some((page) => page.path === m[1]));
  if (!screen) return link;
  const path = pagePathOf(screen, m[2]);
  return path ? `${path}#${m[2]}` : link;
}
