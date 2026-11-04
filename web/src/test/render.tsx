// 테스트용 렌더 — createRoot + act. 라이브러리 없이 DOM 에 붙이고 떼어 낸다.
// 만든 루트는 모두 기억해 두고 setup.ts 의 afterEach 가 cleanup() 으로 떼어 낸다(unmount 를 잊어도 다음 테스트로 새지 않게).
import { act, type ReactElement } from "react";
import { createRoot, type Root } from "react-dom/client";

export interface RenderResult {
  container: HTMLElement;
  unmount(): void;
  rerender(ui: ReactElement): void;
}

interface Mounted {
  root: Root;
  container: HTMLElement;
}

const mounted = new Set<Mounted>();

function teardown(m: Mounted): void {
  if (!mounted.delete(m)) return;
  act(() => m.root.unmount());
  m.container.remove();
}

export function render(ui: ReactElement): RenderResult {
  const container = document.createElement("div");
  document.body.appendChild(container);
  const root = createRoot(container);
  const m: Mounted = { root, container };
  mounted.add(m);
  act(() => root.render(ui));
  return {
    container,
    rerender(next: ReactElement) {
      act(() => root.render(next));
    },
    unmount() {
      teardown(m);
    },
  };
}

/** 남아 있는 모든 루트를 언마운트하고 컨테이너를 떼어 낸다. setup.ts 의 afterEach 가 부른다. */
export function cleanup(): void {
  for (const m of [...mounted]) teardown(m);
}

/** 대기 중인 promise(fetch 응답 등)와 그에 따른 effect·상태 갱신을 act 안에서 비운다.
 *  마이크로태스크만 돌리므로 가짜 타이머(vi.useFakeTimers) 아래서도 멈추지 않는다 —
 *  타이머로 미룬 일은 호출자가 직접 `await vi.runAllTimersAsync()` 로 진행한다. */
export async function flush(): Promise<void> {
  await act(async () => {
    for (let i = 0; i < 20; i++) await Promise.resolve();
  });
}
