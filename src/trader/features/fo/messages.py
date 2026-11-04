"""fo 알림 문구 (파생 레인, Phase 26). 문구는 옛 `notify/messages.py` 그대로이고 틀·금액·매매 표는
커널 `notify.messages` 를 쓴다."""

from __future__ import annotations

from typing import Any

from trader.kernel.notify.messages import DRY_RUN, SIDE, money, render

VEHICLE = {"FUTURES": "선물", "OPTION_BUY": "옵션 매수", "OPTION_SELL": "옵션 매도"}
DIRECTION = {"LONG": "롱", "SHORT": "숏", "FLAT": "관망"}
CONTRACT_SIDE = {"LONG": "매수", "SHORT": "매도"}
FO_INTENT = {
    "OPEN": "진입",
    "RESIZE_UP": "증액",
    "RESIZE_DOWN": "감액",
    "CLOSE": "청산",
    "STOP": "손절",
    "EXPIRY": "만기 청산",
    "SESSION_CLOSE": "마감 청산",
    "RISK_REDUCE": "위험 축소",
}


def fo_label(code: str, name: str | None = None) -> str:
    return f"{name}({code})" if name else code


def fo_order(
    *,
    account: str,
    code: str,
    name: str | None,
    intent: str,
    side: str,
    qty: int,
    order_type: str,
    price: Any,
    status: str,
    order_no: str | None,
    leverage: Any = None,
    stop_pt: Any = None,
    dry_run: bool = False,
    direction: str | None = None,
    vehicle: str | None = None,
    kind: str | None = None,
) -> str:
    """파생 주문 접수·기록 알림. `status` 는 accepted|dry_run|rejected|failed. 방향(시장 뷰)·
    수단·콜/풋을 함께 적어 '매도' 가 숏으로 읽히지 않게 한다(풋 매도 = 상승 베팅)."""
    tag = "파생 주문" if not dry_run else f"파생 주문 · {DRY_RUN}"
    px = "시장가" if order_type == "MARKET" else f"{price}pt 지정가"
    what = fo_what(direction, vehicle, kind)
    lines = [
        *([what] if what else []),
        f"{FO_INTENT.get(intent, intent)} · {SIDE.get(side, side)} {qty}계약 · {px}",
        f"상태 {status}" + (f" · 접수번호 {order_no}" if order_no else ""),
    ]
    if leverage is not None:
        lines.append(f"배수 {float(leverage):.2f}")
    if stop_pt is not None:
        lines.append(f"지수 손절선 {stop_pt}")
    return render(tag, f"{account} · {fo_label(code, name)}", lines)


FO_KIND = {"FUTURES": "선물", "CALL": "콜", "PUT": "풋"}


def fo_what(direction: str | None, vehicle: str | None, kind: str | None) -> str:
    """'롱(상승) · 풋 매도' 처럼 시장 방향과 수단·종류를 한 줄로. 모르면 빈 문자열."""
    if not direction or not vehicle:
        return ""
    d = {"LONG": "롱(상승)", "SHORT": "숏(하락)"}.get(direction, direction)
    if vehicle == "FUTURES":
        return f"{d} · 선물"
    k = FO_KIND.get(kind or "", "옵션")
    act = "매수" if vehicle == "OPTION_BUY" else "매도"
    return f"{d} · {k} {act}"


def fo_reject(*, account: str, code: str, name: str | None, intent: str, reasons: list[str]) -> str:
    return render(
        "파생 판정 거부",
        f"{account} · {fo_label(code, name)}",
        [f"{FO_INTENT.get(intent, intent)} 거부", *reasons[:5]],
    )


def fo_unknown_order(
    *, account: str, code: str, name: str | None, side: str, qty: int, err: Any
) -> str:
    return render(
        "파생 주문 상태 미확인",
        f"{account} · {fo_label(code, name)}",
        [
            f"{SIDE.get(side, side)} {qty}계약 — 요청은 나갔지만 응답을 못 받았어요: {err}",
            "체결 조회로 확정할 때까지 살아 있는 주문으로 봐요. 다시 내지 않아요.",
        ],
    )


def fo_exit_failed(*, account: str, code: str, name: str | None, qty: int, attempts: int) -> str:
    return render(
        "파생 청산 실패",
        f"{account} · {fo_label(code, name)}",
        [
            f"잔량 {qty}계약 — 청산 다리 {attempts}회 재시도 뒤에도 미체결이에요.",
            "매 틱 다시 시도하고 신규 진입은 막아요. 수동 확인이 필요해요.",
        ],
    )


def fo_unknown_holding(*, account: str, code: str, side: str, qty: int) -> str:
    return render(
        "파생 잔고 출처 불명",
        f"{account} · {code}",
        [
            f"{CONTRACT_SIDE.get(side, side)} {qty}계약 — 원장에 없는 보유예요.",
            "자동 주문은 내지 않아요. `trader fo adopt` 로 승계하거나 수동으로 정리하세요.",
        ],
    )


def fo_field_unverified(*, account: str, items: list[str]) -> str:
    return render(
        "파생 잔고 미확정",
        account,
        [
            *[f"미확정 행: {s}" for s in items[:5]],
            "방향·종류를 확정할 수 없어 어떤 자동 주문의 대상도 아니에요. 신규 진입을 막아요.",
        ],
    )


def fo_recall(*, account: str, code: str, name: str | None, reason: str, cancelled: int) -> str:
    return render(
        "파생 진입 회수",
        f"{account} · {fo_label(code, name)}",
        [f"사유: {reason}", f"살아 있는 진입 다리 {cancelled}건 취소 요청·확정"],
    )


def fo_settlement(*, account: str, code: str, name: str | None, pnl: Any) -> str:
    """만기 정산 확인. `pnl` 이 None 이면 손익 미확정(정산가·행사 내역은 체결·잔고로 확인)."""
    line = (
        f"만기 정산 손익 {money(pnl, 'KR')}"
        if pnl is not None
        else "만기로 잔고에서 사라졌어요 — 정산 손익은 HTS 체결·잔고로 확인하세요"
    )
    return render("파생 정산", f"{account} · {fo_label(code, name)}", [line])


def fo_fill_anomaly(
    *, account: str, code: str, name: str | None, leg_qty: int, filled: int, order_no: str
) -> str:
    """체결 조회의 누적 체결이 다리 주문 수량보다 크다(계보 모순) — 포지션을 unknown 으로 두고 다른
    계보는 계속 동기화한다(단계 2 리뷰 C2-6)."""
    return render(
        "파생 체결 모순",
        f"{account} · {fo_label(code, name)}",
        [
            f"주문 {order_no}: 누적 체결 {filled}계약 > 다리 수량 {leg_qty}계약",
            "이 포지션은 unknown 으로 두고 사람이 adopt·close 로 정리해요.",
        ],
    )


def fo_position_mismatch(
    *, account: str, code: str, name: str | None, ledger_qty: int, broker_qty: int | None
) -> str:
    """원장 포지션과 브로커 잔고가 다르다(브로커가 진실 — 원장을 맞추고 신규를 막는다)."""
    seen = "잔고에 없음" if broker_qty is None else f"브로커 {broker_qty}계약"
    return render(
        "파생 포지션 불일치",
        f"{account} · {fo_label(code, name)}",
        [f"원장 {ledger_qty}계약 ≠ {seen}", "unknown 으로 두고 사람이 adopt·close 로 정리"],
    )


def fo_exit(
    *, account: str, code: str, name: str | None, intent: str, reason: str, qty: int
) -> str:
    """보호선·만기·마감 청산 발동(엔진). 주문 접수는 `fo_order` 가 따로 알린다."""
    return render(
        f"파생 {FO_INTENT.get(intent, intent)}",
        f"{account} · {fo_label(code, name)}",
        [f"사유: {reason}", f"보유 {qty}계약 청산 다리를 냅니다"],
    )


def fo_halted(*, account: str, loss: Any, limit: Any) -> str:
    """일일 손실 한도 도달 — 오늘 신규 진입 금지 + 살아 있는 진입 다리 회수."""
    return render(
        "파생 일일 손실 한도",
        account,
        [
            f"오늘 손익 {money(loss, 'KR')} ≤ 한도 −{money(limit, 'KR')}",
            "오늘은 신규 진입을 막고 미체결 진입을 회수해요. 보유분 감시·청산은 계속해요.",
        ],
    )


def fo_risk_reduce(
    *, account: str, code: str, name: str | None, reason: str, held: int, target: int
) -> str:
    """유지 조건(스트레스·배수·증거금·수명 예산·현금 버퍼) 위반으로 축소."""
    return render(
        "파생 위험 축소",
        f"{account} · {fo_label(code, name)}",
        [f"사유: {reason}", f"{held}계약 → {target}계약"],
    )


def fo_maintain_warning(*, account: str, reasons: list[str]) -> str:
    """15:00 전 스트레스 초과 — 아직 줄이지 않고 알리기만(15:00 확정 검사에서 축소)."""
    return render(
        "파생 유지 조건 주의",
        account,
        [*reasons[:4], "15:00 확정 검사에서도 넘으면 축소해요."],
    )
