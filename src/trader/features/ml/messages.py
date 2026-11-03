"""ml 사용자 대면 문구(학습 결과·수정주가 변경). 공용 틀은 커널 `messages.render`·`event`."""

from __future__ import annotations

from trader.kernel.notify.messages import event, render


def ml_trained(
    *,
    kind: str,
    version: str | None,
    trained: bool,
    detail: str | None,
    promoted: bool | None,
    note: str = "",
) -> str:
    """학습 결과. promoted None(학습 안 됨·수동 승격 모델)이면 승격 줄 없음."""
    head = f"{kind} {version or '—'}: {'학습 완료' if trained else '데이터 부족'}"
    if detail:
        head += f" · {detail}"
    lines = []
    if promoted is not None:
        lines.append("승격했어요" if promoted else "승격 안 함(기준 미달)")
    return render("ML 학습", head, [*lines, note])


def adjusted_prices(market: str, tickers: list[str]) -> str:
    """수정주가 소급 변경 감지 → 해당 종목 재백필(피처·라벨 절벽 방지)."""
    return event(
        "수정주가 변경",
        f"{market} {len(tickers)}종목 과거 일봉을 다시 받았어요",
        ", ".join(tickers[:10]) + (" …" if len(tickers) > 10 else ""),
    )
