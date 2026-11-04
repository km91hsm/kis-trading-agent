// node:fs 대신 Vite `?raw` 로 읽는다(@types/node 없이 typecheck 통과, jsdom 환경에서도 동작).
import css from "../../styles/tokens.css?raw";
import { expect, test } from "vitest";
const REQUIRED = [
  "--bg", "--bg-elev", "--fg", "--fg-muted", "--line", "--accent",
  "--ok", "--warn", "--fail", "--up", "--down", "--live", "--live-bg",
  "--space-1", "--space-2", "--space-3", "--space-4", "--space-6",
  "--radius", "--font", "--font-mono", "--z-sticky",
];
test("tokens.css 가 필수 변수를 :root 와 [data-theme=dark] 둘 다 정의한다", () => {
  for (const v of REQUIRED) {
    expect(css.match(new RegExp(`${v}:`, "g"))?.length ?? 0).toBeGreaterThanOrEqual(2);
  }
});
test("상태색·실전색은 light/dark 에서 값이 다르다", () => {
  const light = css.slice(css.indexOf(":root"), css.indexOf("[data-theme=\"dark\"]"));
  const dark = css.slice(css.indexOf("[data-theme=\"dark\"]"));
  for (const v of ["--bg", "--fg", "--live-bg"]) {
    expect(light.match(new RegExp(`${v}: *([^;]+);`))![1]).not.toBe(dark.match(new RegExp(`${v}: *([^;]+);`))![1]);
  }
});
