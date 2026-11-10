/** 비동기 콘텐츠와 접힌 상세를 따라 절로 이동한다. 사용자 입력 뒤에는 위치를 강제하지 않는다. */
export function followHashTarget(hash: string, root: HTMLElement): () => void {
  let id: string;
  try { id = decodeURIComponent(hash.replace(/^#/, "")); } catch { return () => {}; }
  if (!id) return () => {};
  let active = true;
  let queued: ReturnType<typeof setTimeout> | undefined;
  let deadline: ReturnType<typeof setTimeout> | undefined;
  const inputs = ["wheel", "touchstart", "pointerdown", "keydown"] as const;
  const align = () => {
    queued = undefined;
    if (!active) return;
    const target = document.getElementById(id);
    if (!target || !root.contains(target) || target.closest("[hidden]")) return;
    for (let node: HTMLElement | null = target; node && root.contains(node); node = node.parentElement) {
      if (node instanceof HTMLDetailsElement && !node.open) node.open = true;
    }
    target.querySelectorAll<HTMLDetailsElement>(":scope > details").forEach((details) => { if (!details.open) details.open = true; });
    const bar = document.querySelector<HTMLElement>(".statusbar");
    if (bar) target.style.scrollMarginTop = `${bar.getBoundingClientRect().height + 12}px`;
    target.scrollIntoView?.({ block: "start", behavior: "auto" });
  };
  const schedule = () => { if (active && queued === undefined) queued = setTimeout(align, 0); };
  const changes = new MutationObserver(schedule);
  const sizes = typeof ResizeObserver === "function" ? new ResizeObserver(schedule) : null;
  const stop = () => {
    active = false; clearTimeout(queued); clearTimeout(deadline);
    changes.disconnect(); sizes?.disconnect();
    inputs.forEach((event) => window.removeEventListener(event, stop, true));
  };
  changes.observe(root, { subtree: true, childList: true, attributes: true, attributeFilter: ["hidden", "open"] });
  sizes?.observe(root);
  inputs.forEach((event) => window.addEventListener(event, stop, { capture: true, passive: true }));
  // 최초 로딩의 위치 보정만 수행하고 지속적인 SSE 갱신에는 따라가지 않는다.
  deadline = setTimeout(stop, 15_000);
  align();
  return stop;
}
