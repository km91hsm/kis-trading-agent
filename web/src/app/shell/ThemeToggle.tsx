// 테마 토글 — 시스템 → 라이트 → 다크 → 시스템. 저장은 themeStore, 시스템일 때만 OS 설정 변화를 따른다.
// 브라우저 띠 색(meta theme-color)도 맞춘다: index.html 의 두 meta 가 선언한 라이트/다크 값을 읽어 고른 쪽으로 통일.
import { useEffect, useState } from "react";
import { Icon } from "../../components/Icon";
import { applyTheme, prefersDark, resolveTheme, themeStore, watchSystemTheme, type ResolvedTheme, type Theme } from "../../lib/theme";

const NEXT: Record<Theme, Theme> = { system: "light", light: "dark", dark: "system" };
const LABEL: Record<Theme, string> = { system: "시스템", light: "라이트", dark: "다크" };

function syncThemeColor(resolved: ResolvedTheme): void {
  const metas = Array.from(document.querySelectorAll<HTMLMetaElement>('meta[name="theme-color"]'));
  for (const m of metas) m.dataset.original ??= m.content; // 처음 본 선언값을 보관
  const pick = metas.find((m) => (m.media || "").includes(resolved));
  if (!pick) return;
  for (const m of metas) m.content = pick.dataset.original!;
}

export function ThemeToggle() {
  const [theme, setTheme] = useState<Theme>(() => themeStore.get());
  const [dark, setDark] = useState(() => prefersDark());
  const resolved = resolveTheme(theme, dark);

  useEffect(() => {
    if (theme !== "system") return;
    setDark(prefersDark());
    return watchSystemTheme(setDark);
  }, [theme]);

  useEffect(() => {
    applyTheme(resolved);
    syncThemeColor(resolved);
  }, [resolved]);

  const next = NEXT[theme];
  return (
    <button
      type="button"
      className="theme-toggle"
      data-theme-toggle={theme}
      aria-label={`테마: ${LABEL[theme]} (눌러서 ${LABEL[next]})`}
      title={`테마: ${LABEL[theme]}`}
      onClick={() => {
        themeStore.set(next);
        setTheme(next);
      }}
    >
      <Icon name={resolved === "dark" ? "moon" : "sun"} />
      <span className="theme-toggle-label">{LABEL[theme]}</span>
    </button>
  );
}
