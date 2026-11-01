"""SSE 키 목록의 TypeScript 파일 생성 — 프런트가 키를 손으로 적다 어긋나지 않게 한다."""

from __future__ import annotations

from pathlib import Path

from trader.kernel.registry.types import Feature
from trader.kernel.web.sse import KERNEL_SSE_KEYS

REL_PATH = Path("web/src/generated/sse-keys.ts")


def _ts_list(keys: list[str]) -> str:
    return "[" + ", ".join(f'"{k}"' for k in keys) + "]"


def sse_keys_ts(features: list[Feature]) -> str:
    """커널 키 + 기능 `sse_keys` 로 `sse-keys.ts` 본문을 만든다 (끝에 개행 한 줄)."""
    keys = list(KERNEL_SSE_KEYS)
    noisy = list(KERNEL_SSE_KEYS)  # 데몬 하트비트는 잦게 바뀌는 키라 전역 갱신에서 뺀다
    for f in features:
        for k in f.sse_keys:
            keys.append(k.key)
            if k.noisy:
                noisy.append(k.key)
    return (
        "// 자동 생성 파일 — 수정 금지. `uv run python -m trader.kernel.web.gen` 로 다시 만든다.\n"
        f"export const SSE_KEYS = {_ts_list(keys)} as const;\n"
        "export type SseKey = (typeof SSE_KEYS)[number];\n"
        f"export const NOISY_KEYS: SseKey[] = {_ts_list(noisy)};\n"
    )


def main() -> None:
    from trader.kernel.platform import paths
    from trader.kernel.registry import discover

    out = paths.project_root() / REL_PATH
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(sse_keys_ts(discover()), encoding="utf-8", newline="\n")


if __name__ == "__main__":
    main()
