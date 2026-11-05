"""트리거 처리 표시: 재평가 세션 결과를 `triggers.session_id` 에 남길 값으로 바꾼다."""

from __future__ import annotations

from trader.kernel.session.runner import SessionResult


def handled_marker(res: SessionResult) -> str | None:
    """triggers.session_id 에 남길 값. 세션이 떴어도 실패(타임아웃·예산·오류)면 '실패:' 접두사를
    붙여 운영일 1회 제한에서 빼고 다음 간격에 다시 시도할 수 있게 한다(200자 절단)."""
    if res.ok:
        return res.session_id
    return f"실패: {res.error or 'unknown'} ({res.session_id or '-'})"[:200]
