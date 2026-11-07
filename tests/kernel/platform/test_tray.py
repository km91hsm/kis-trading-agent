"""트레이/메뉴바: 메뉴 모델과 제목은 순수 함수(pystray 없이 검사), 상태는 원장에서 읽는다."""

import os
import signal
import sys
import threading

import pytest

from trader.kernel.ledger import db
from trader.kernel.ledger.control import get_flag, set_flag
from trader.kernel.platform import tray
from trader.kernel.registry import discover
from trader.kernel.registry.contracts import all_migrations


@pytest.fixture
def conn(project):
    c = db.connect()
    db.init_schema(c, all_migrations(discover()))
    try:
        yield c
    finally:
        c.close()


def _state(**over):
    base = dict(paused=False, pending_approvals=0, last_error=None, web_url="http://127.0.0.1:8787")
    base.update(over)
    return tray.TrayState(**base)


def test_menu_model_order_and_labels():
    items = tray.menu_model(_state())
    assert [i.key for i in items] == [
        "open_web", "toggle_pause", "open_logs", "open_data", "autostart", "quit",
    ]  # fmt: skip
    assert [i.label for i in items] == [
        "웹 UI 열기", "일시정지", "로그 폴더 열기", "데이터 폴더 열기",
        "로그인 시 자동 시작", "종료",
    ]  # fmt: skip
    assert all(i.enabled for i in items)


def test_menu_model_autostart_checked_and_disabled_when_unavailable():
    item = next(i for i in tray.menu_model(_state(autostart=True)) if i.key == "autostart")
    assert item.checked is True and item.enabled is True
    items = tray.menu_model(_state(autostart_available=False))
    item = next(i for i in items if i.key == "autostart")
    assert item.checked is False and item.enabled is False


def test_menu_model_paused_shows_resume_checked():
    item = next(i for i in tray.menu_model(_state(paused=True)) if i.key == "toggle_pause")
    assert item.label == "재개" and item.checked is True


def test_title_text_priority():
    assert tray.title_text(_state()) == "trader · 실행 중"
    assert tray.title_text(_state(pending_approvals=2)) == "trader · 미승인 2"
    assert tray.title_text(_state(paused=True, pending_approvals=2)) == "trader · 일시정지"


def test_read_state_from_ledger(conn):
    set_flag(conn, "paused", True)
    conn.execute(
        "INSERT INTO approvals(account_id, status, requested_at, expires_at, order_json, "
        "fingerprint) "
        "VALUES ('paper-main', 'pending', '2026-10-23T00:00:00+00:00', "
        "'2026-10-23T00:30:00+00:00', '{}', 'fp-1')"
    )
    conn.execute(
        "INSERT INTO approvals(account_id, status, requested_at, expires_at, order_json, "
        "fingerprint) "
        "VALUES ('paper-main', 'approved', '2026-10-23T00:00:00+00:00', "
        "'2026-10-23T00:30:00+00:00', '{}', 'fp-2')"
    )
    conn.execute(
        "INSERT INTO runs(kind, market, started_at, status, error) "
        "VALUES ('KR_scan_analyze','KR','2026-10-23T00:00:00+00:00','error','세션 실패')"
    )
    conn.commit()
    st = tray.read_state(lambda: conn, "http://127.0.0.1:8787", close=False)
    assert st.paused is True and st.pending_approvals == 1 and st.last_error == "세션 실패"
    assert st.web_url == "http://127.0.0.1:8787"


def test_toggle_pause_uses_injected_setter(conn):
    """기본은 control 플래그를 직접 뒤집지만, 주입된 setter 가 있으면 그것만 부른다
    (accounts 포트의 set_paused(channel="tray") 가 control_log 를 남기도록)."""
    calls: list = []
    new = tray.toggle_pause(lambda: conn, set_fn=lambda c, v: calls.append((c, v)), close=False)
    assert new is True and calls == [(conn, True)]
    assert get_flag(conn, "paused", False) is False  # 기본 setter 는 쓰이지 않았다

    assert tray.toggle_pause(lambda: conn, close=False) is True  # 기본: set_flag
    assert get_flag(conn, "paused", False) is True
    assert tray.toggle_pause(lambda: conn, close=False) is False
    assert get_flag(conn, "paused", False) is False


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX 시그널")
def test_watch_signals_delivers_while_python_handler_cannot_run():
    """트레이 모드의 메인 스레드는 AppKit 런루프에 막혀 파이썬 핸들러가 돌지 않는다 →
    wakeup fd 를 읽는 감시 스레드가 시그널 번호를 넘겨야 한다."""
    prev = signal.signal(signal.SIGTERM, lambda *_: None)
    got: list[int] = []
    seen = threading.Event()

    def on_signal(signum: int) -> None:
        got.append(signum)
        seen.set()

    release = tray.watch_signals(on_signal, (signal.SIGTERM,))
    try:
        os.kill(os.getpid(), signal.SIGTERM)
        assert seen.wait(5.0)
        assert got == [signal.SIGTERM]
    finally:
        release()
        signal.signal(signal.SIGTERM, prev)
    assert signal.set_wakeup_fd(-1) == -1  # 해제가 이전 값(-1)을 복원했다


def test_watch_signals_off_main_thread_is_noop():
    out: list = []
    t = threading.Thread(target=lambda: out.append(tray.watch_signals(lambda s: None, ())))
    t.start()
    t.join(5)
    assert not t.is_alive()
    out[0]()  # 예외 없이 no-op 해제


class _FakeIcon:
    """워커 스레드 결과를 고정 폴링 대신 이벤트로 기다린다(T10 — 부하 큰 CI 플레이크 방지)."""

    def __init__(self):
        self.title = ""
        self.stopped = False
        self.menu_updates = 0
        self.stopped_evt = threading.Event()
        self.updated_evt = threading.Event()

    def stop(self):
        self.stopped = True
        self.stopped_evt.set()

    def update_menu(self):
        self.menu_updates += 1
        self.updated_evt.set()


def _controller(state_fn, *, on_toggle=lambda: None, on_quit=lambda: None, on_autostart=None):
    icon = _FakeIcon()
    ctl = tray.TrayController(
        state_fn=state_fn,
        on_toggle_pause=on_toggle,
        on_quit=on_quit,
        initial=state_fn(),
        run_on_main=lambda fn: fn(),
        on_toggle_autostart=on_autostart,
    )
    ctl.icon = icon
    return ctl, icon


def test_controller_labels_read_cached_state_without_db_calls():
    """메뉴 라벨·checked 는 캐시만 읽는다 — 메인 스레드(AppKit 런루프)에서 SQLite 를 열지 않는다."""
    calls = {"n": 0}

    def state_fn():
        calls["n"] += 1
        return _state(paused=calls["n"] > 1)

    ctl, _icon = _controller(state_fn)
    assert calls["n"] == 1
    for _ in range(10):
        ctl.label("toggle_pause")
        ctl.checked("toggle_pause")
        ctl.title()
    assert calls["n"] == 1 and ctl.label("toggle_pause") == "일시정지"
    ctl.refresh_state()
    assert calls["n"] == 2 and ctl.label("toggle_pause") == "재개" and ctl.checked("toggle_pause")


def test_quit_action_returns_immediately_and_stops_icon_after_shutdown():
    """종료는 워커 스레드에서 돈다. 메뉴를 누른 메인 스레드는 바로 돌아오고 제목이 '종료 중' 이
    되며, shutdown 이 끝난 뒤에 아이콘이 내려간다(이전엔 메인 스레드가 shutdown(wait=True) 에
    막혀 비치볼)."""
    started = threading.Event()
    release = threading.Event()

    def on_quit():
        started.set()
        release.wait(5)

    ctl, icon = _controller(lambda: _state(), on_quit=on_quit)
    ctl.quit_action(icon, None)
    assert started.wait(2)
    assert icon.title == tray.QUITTING_TITLE and icon.stopped is False
    ctl.quit_action(icon, None)  # 두 번 눌러도 한 번만
    release.set()
    assert icon.stopped_evt.wait(5)
    assert icon.stopped is True


def test_pause_action_toggles_on_worker_and_refreshes_title():
    flag = {"paused": False}

    def toggle():
        flag["paused"] = not flag["paused"]

    ctl, icon = _controller(lambda: _state(paused=flag["paused"]), on_toggle=toggle)
    ctl.pause_action(icon, None)
    assert icon.updated_evt.wait(5)  # 제목을 바꾼 뒤 메뉴를 갱신한다
    assert icon.title == "trader · 일시정지" and ctl.checked("toggle_pause") is True
    assert icon.menu_updates >= 1


def test_on_main_falls_back_to_direct_call_without_appkit(monkeypatch):
    # macOS 엔 pyobjc 가 있어 callAfter 로 런루프에 넘어간다(테스트엔 런루프 없음) → import 차단
    monkeypatch.setitem(sys.modules, "PyObjCTools", None)
    done = []
    tray.on_main(lambda: done.append(1))
    assert done == [1]


def test_autostart_action_toggles_on_worker_and_refreshes_check():
    reg = {"on": False}

    def toggle():
        reg["on"] = not reg["on"]

    ctl, icon = _controller(lambda: _state(autostart=reg["on"]), on_autostart=toggle)
    assert ctl.checked("autostart") is False
    ctl.autostart_action(icon, None)
    assert icon.updated_evt.wait(5)
    assert reg["on"] is True and ctl.checked("autostart") is True


def test_autostart_action_noop_when_unavailable():
    called = []
    ctl, icon = _controller(
        lambda: _state(autostart_available=False), on_autostart=lambda: called.append(1)
    )
    assert ctl.enabled("autostart") is False
    ctl.autostart_action(icon, None)
    assert called == [] and icon.menu_updates == 0
