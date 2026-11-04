// 테스트용 렌더 — createRoot + act. 라이브러리 없이 DOM 에 붙이고 떼어 낸다.
import { act, type ReactElement } from "react";
import { createRoot } from "react-dom/client";

export interface RenderResult {
  container: HTMLElement;
  unmount(): void;
  rerender(ui: ReactElement): void;
}

export function render(ui: ReactElement): RenderResult {
  const container = document.createElement("div");
  document.body.appendChild(container);
  const root = createRoot(container);
  act(() => root.render(ui));
  return {
    container,
    rerender(next: ReactElement) {
      act(() => root.render(next));
    },
    unmount() {
      act(() => root.unmount());
      container.remove();
    },
  };
}

/** 대기 중인 promise(fetch 응답 등)와 그에 따른 effect·상태 갱신을 act 안에서 비운다. */
export async function flush(): Promise<void> {
  await act(async () => {
    for (let i = 0; i < 5; i++) await new Promise<void>((r) => setTimeout(r, 0));
  });
}
