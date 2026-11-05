"""markers.handled_marker: 트리거 처리 표시(성공은 세션 id, 실패는 '실패:' 접두사)."""

from trader.kernel.session.markers import handled_marker
from trader.kernel.session.runner import SessionResult


def test_handled_marker_success_and_failure_prefix():
    assert handled_marker(SessionResult(ok=True, session_id="s1")) == "s1"
    assert handled_marker(SessionResult(ok=False, error="타임아웃", session_id="s2")) == (
        "실패: 타임아웃 (s2)"
    )
    assert handled_marker(SessionResult(ok=False)) == "실패: unknown (-)"
    assert len(handled_marker(SessionResult(ok=False, error="x" * 500)) or "") == 200
