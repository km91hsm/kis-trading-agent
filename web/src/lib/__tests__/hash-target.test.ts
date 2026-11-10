import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { followHashTarget } from "../hash-target";

let stop: (() => void) | undefined;
let root: HTMLElement;
const scroll = vi.fn();
const oldScroll = Element.prototype.scrollIntoView;
beforeEach(() => {
  vi.useFakeTimers(); scroll.mockClear();
  Element.prototype.scrollIntoView = scroll;
  root = document.createElement("main"); document.body.append(root);
});
afterEach(() => { stop?.(); root.remove(); Element.prototype.scrollIntoView = oldScroll; vi.useRealTimers(); });
const changed = async () => { await Promise.resolve(); await vi.advanceTimersByTimeAsync(20); };

test("늦게 나타난 anchor와 앞쪽 비동기 콘텐츠가 추가된 뒤 목표 위치를 다시 맞춘다", async () => {
  stop = followHashTarget("#fo", root);
  root.innerHTML = '<section id="fo" tabindex="-1">파생 설정</section>';
  await changed();
  expect(scroll).toHaveBeenCalled();
  scroll.mockClear();
  root.prepend(document.createElement("article")); await changed();
  expect(scroll).toHaveBeenCalled();
});

test("접힌 조상은 펼치고 사용자가 스크롤하면 이후 강제 이동을 멈춘다", async () => {
  root.innerHTML = '<details><summary>고급 분석</summary><section id="rl">분석</section></details>';
  stop = followHashTarget("#rl", root); await changed();
  expect(root.querySelector("details")?.open).toBe(true);
  scroll.mockClear(); window.dispatchEvent(new Event("wheel"));
  root.prepend(document.createElement("article")); await changed();
  expect(scroll).not.toHaveBeenCalled();
});

test("절 바로 아래 접힌 상세도 펼쳐서 실제 내용을 보여 준다", async () => {
  root.innerHTML = '<section id="calibration"><details><summary>판정 보정</summary>분석 내용</details></section>';
  stop = followHashTarget("#calibration", root); await changed();
  expect(root.querySelector("details")?.open).toBe(true);
});

test("숨긴 기능을 강제로 열지 않고 잘못된 인코딩도 무시한다", async () => {
  root.innerHTML = '<section hidden id="fo">미사용</section>';
  stop = followHashTarget("#fo", root); await changed(); expect(scroll).not.toHaveBeenCalled(); stop();
  expect(() => { stop = followHashTarget("#%E0%A4%A", root); }).not.toThrow();
});
