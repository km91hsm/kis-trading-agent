// 옛 `web/src/theme.ts` 이식
// 테마(라이트/다크/시스템) 저장과 적용. 저장 키는 trader.theme, 적용은 data-theme 속성.

export type Theme = "system" | "light" | "dark";
export type ResolvedTheme = "light" | "dark";

const KEY = "trader.theme";
const THEMES: readonly Theme[] = ["system", "light", "dark"];
const QUERY = "(prefers-color-scheme: dark)";

export const themeStore = {
  get(): Theme {
    try {
      const v = localStorage.getItem(KEY);
      return THEMES.includes(v as Theme) ? (v as Theme) : "system";
    } catch {
      return "system";
    }
  },
  set(t: Theme): void {
    try {
      localStorage.setItem(KEY, t);
    } catch {
      /* 저장 불가 환경이면 저장 없이 진행 */
    }
  },
};

export function resolveTheme(t: Theme, prefersDark: boolean): ResolvedTheme {
  if (t === "system") return prefersDark ? "dark" : "light";
  return t;
}

export function prefersDark(): boolean {
  try {
    return window.matchMedia(QUERY).matches;
  } catch {
    return false;
  }
}

export function applyTheme(resolved: ResolvedTheme, root: HTMLElement = document.documentElement): void {
  root.setAttribute("data-theme", resolved);
}

/** OS 다크 선호가 바뀔 때 cb(prefersDark) 를 부른다. 해제 함수를 돌려준다. */
export function watchSystemTheme(cb: (prefersDark: boolean) => void): () => void {
  let mq: MediaQueryList;
  try {
    mq = window.matchMedia(QUERY);
  } catch {
    return () => {};
  }
  const onChange = (e: MediaQueryListEvent) => cb(e.matches);
  mq.addEventListener("change", onChange);
  return () => mq.removeEventListener("change", onChange);
}
