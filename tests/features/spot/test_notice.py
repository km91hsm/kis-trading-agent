"""체결통보 리스너(Stock D-060): HTS ID 로 국내·해외 통보 구독, 통보 프레임 → 체결 동기화 요청(5초에
한 번), 핑퐁, HTS ID 거절이면 멈춤, 접속키 거절이면 새 키, 접속 실패 백오프.

옛 `tests/orchestrator/test_notice.py` 9건 이식(단언 그대로) + 데몬 잡 수명(보장·teardown) 1건.
웹소켓은 가짜 소켓 객체만 쓴다(`connect` 주입)."""

import json
import threading
from datetime import UTC, datetime
from types import SimpleNamespace

from tests.features.accounts.conftest import make_account
from trader.features.spot import jobs as spot_jobs
from trader.features.spot import notice
from trader.features.spot.notice import NoticeListener
from trader.kernel.config import Secrets
from trader.kernel.daemon import queue as jobq
from trader.kernel.ledger import db
from trader.kernel.registry import discover


class Conn:
    def __init__(self, inbox=()):
        self.sent, self.inbox, self.closed = [], list(inbox), False

    def send(self, t):
        self.sent.append(t)

    def recv(self, timeout=None):
        if self.inbox:
            return self.inbox.pop(0)
        raise TimeoutError

    def close(self):
        self.closed = True


def _listener(conns, *, env="paper"):
    clock = [0.0]
    fired, invalid = [], []
    made = []

    def connect(url):
        c = conns.pop(0)
        if isinstance(c, Exception):
            raise c
        made.append((url, c))
        return c

    lst = NoticeListener(
        env=env,
        hts_id="myhts",
        approval=lambda: "ak",
        invalidate=lambda: invalid.append(1),
        on_notice=fired.append,
        connect=connect,
        clock=lambda: clock[0],
    )
    return lst, clock, fired, invalid, made


def test_subscribes_both_notice_trs_and_fires_debounced():
    c = Conn(["1|H0STCNI9|001|ENCRYPTED", "1|H0STCNI9|001|ENCRYPTED2"])
    lst, clock, fired, _, made = _listener([c])
    lst.step()
    assert made[0][0].endswith(":31000")  # 모의 웹소켓
    subs = [json.loads(m)["body"]["input"] for m in c.sent]
    assert subs == [
        {"tr_id": "H0STCNI9", "tr_key": "myhts"},
        {"tr_id": "H0GSCNI9", "tr_key": "myhts"},
    ]
    lst.step()
    lst.step()  # 같은 5초 안 두 번째 통보 → 동기화 요청은 한 번
    assert fired == ["paper"] and lst.notices == 2
    c.inbox.append("1|H0GSCNI9|001|X")
    clock[0] = 6.0
    lst.step()
    assert fired == ["paper", "paper"]


def test_pingpong_and_hts_rejection_stops():
    ping = json.dumps({"header": {"tr_id": "PINGPONG"}})
    bad = json.dumps(
        {
            "header": {"tr_id": "H0STCNI0", "tr_key": "x"},
            "body": {"rt_cd": "1", "msg1": "invalid htsid"},
        }
    )
    c = Conn([ping, bad])
    lst, _, fired, _, made = _listener([c], env="real")
    lst.step()
    lst.step()
    assert c.sent[-1] == ping and made[0][0].endswith(":21000")
    lst.step()
    assert lst.rejected == "invalid htsid" and c.closed and not lst.step()


def test_approval_rejected_and_connect_backoff():
    bad = json.dumps({"header": {"tr_id": "H0STCNI9"}, "body": {"rt_cd": "1", "msg1": "NOT FOUND"}})
    lst, clock, _, invalid, made = _listener([OSError("x"), Conn([bad]), Conn()])
    assert not lst.step() and not made  # 실패 → 1초 백오프
    assert not lst.step()
    clock[0] = 1.5
    lst.step()  # 접속
    lst.step()  # 접속키 거절 → 무효화
    assert invalid == [1] and lst.status()["connected"] is False


def _queue_deps():
    return SimpleNamespace(
        conn_factory=db.connect,
        queue=SimpleNamespace(kinds=jobq.kinds_of(discover())),
        now=lambda: datetime(2026, 10, 27, 0, 0, tzinfo=UTC),
    )


def test_daemon_handler_enqueues_fills_sync_once(conn):
    h = spot_jobs.notice_handler(_queue_deps())
    h("paper")
    h("paper")  # 이미 대기 중이면 그것으로 충분
    rows = conn.execute("SELECT kind, source FROM job_requests").fetchall()
    assert [(r["kind"], r["source"]) for r in rows] == [("fills_sync", "notice:paper")]
    fast = {q.kind for q in spot_jobs.QUEUE_KINDS if q.lane == "fast"}
    assert "fills_sync" in fast


def test_trailing_notice_is_not_lost_and_running_sync_retries():
    """디바운스로 미룬 마지막 통보도 간격이 지나면 동기화하고, 동기화가 실행 중이면 다시 요청."""
    c = Conn(["1|H0STCNI9|001|A"])
    lst, clock, fired, _, _ = _listener([c])
    answers = [True, False, True]
    lst._on_notice = lambda env: fired.append(env) or answers.pop(0)
    lst.step()  # 접속
    lst.step()  # 첫 통보 → 즉시
    clock[0] = 4.0
    c.inbox.append("1|H0STCNI9|001|B")
    lst.step()  # 5초 안 → 미룸
    assert len(fired) == 1
    clock[0] = 5.5
    lst.step()  # 수신 없음(타임아웃)이어도 미룬 것을 보낸다 — 실행 중(False) 이라 남김
    clock[0] = 11.0
    lst.step()
    assert len(fired) == 3 and not lst._pending


def test_transient_error_reconnects_ping_failure_and_ack_catches_up():
    err = json.dumps(
        {"header": {"tr_id": "H0STCNI9"}, "body": {"rt_cd": "1", "msg1": "SERVER BUSY"}}
    )
    ok = json.dumps(
        {"header": {"tr_id": "H0STCNI9"}, "body": {"rt_cd": "0", "msg1": "SUBSCRIBE SUCCESS"}}
    )

    class PingFail(Conn):
        def send(self, t):
            if "PINGPONG" in t:
                raise ConnectionError("x")
            super().send(t)

    ping = json.dumps({"header": {"tr_id": "PINGPONG"}})
    lst, clock, fired, _, made = _listener([Conn([err]), PingFail([ping]), Conn([ok])])
    lst.step()
    lst.step()  # 일시 오류 → 영구 거절 아님, 다시 접속
    assert lst.rejected is None and not lst.status()["connected"]
    clock[0] = 2.0
    lst.step()
    lst.step()  # 핑 응답 실패 → 끊김으로 처리(스레드 유지)
    assert not lst.status()["connected"]
    clock[0] = 10.0
    lst.step()
    lst.step()  # 구독 성공 응답 → 끊긴 동안 체결 보충 동기화
    assert fired == ["paper"]


def test_subscribe_send_failure_closes_socket():
    class SendFail(Conn):
        def send(self, t):
            raise ConnectionError("x")

    c = SendFail()
    lst, _, _, _, _ = _listener([c])
    assert not lst.step() and c.closed


def test_real_listener_connects_only_when_enabled_and_yields_session():
    """Codex(PR #46): KIS 는 앱키당 웹소켓 세션 하나 — 실전 리스너는 실전 계좌가 주문을 낼 수
    있을 때만 붙고, 그동안만 holds_session() 이 참(같은 프로세스 시세 허브가 비켜선다)."""
    on = [False]
    c = Conn()
    clock = [0.0]
    made = []
    lst = NoticeListener(
        env="real",
        hts_id="myhts",
        approval=lambda: "ak",
        invalidate=lambda: None,
        on_notice=lambda env: True,
        connect=lambda url: made.append(c) or c,
        clock=lambda: clock[0],
        enabled=lambda: on[0],
    )
    assert lst.step() is False and made == [] and lst.holds_session() is False
    on[0] = True
    lst.step()  # 캐시(ENABLED_TTL_S) 안이라 아직 끔
    assert made == []
    clock[0] += notice.ENABLED_TTL_S
    lst.step()
    assert made == [c] and lst.holds_session() is True
    on[0] = False
    clock[0] += notice.ENABLED_TTL_S
    lst.step()
    assert c.closed and lst.holds_session() is False


def test_appkey_in_use_backs_off_instead_of_ignoring():
    busy = json.dumps(
        {"header": {"tr_id": "H0STCNI0"}, "body": {"rt_cd": "1", "msg1": "ALREADY IN USE appkey"}}
    )
    c1, c2 = Conn([busy]), Conn()
    lst, clock, _, _, made = _listener([c1, c2], env="real")
    lst.step()  # 접속·구독
    lst.step()  # 사용 중 → 끊고 백오프
    assert c1.closed and lst.rejected is None
    lst.step()
    assert len(made) == 1  # 백오프 동안 다시 붙지 않는다
    clock[0] += 2
    lst.step()
    assert len(made) == 2


# --- 데몬 잡 수명(신규): env 별 리스너 1개, 실전은 정책 off 면 붙지 않음, teardown 이 멈춘다


class SlowConn(Conn):
    """recv 가 잠깐 기다린 뒤 타임아웃 — 스레드 루프가 바쁘게 돌지 않게."""

    def __init__(self, inbox=()):
        super().__init__(inbox)
        self.subscribed = threading.Event()

    def send(self, t):
        super().send(t)
        if len(self.sent) >= 2:
            self.subscribed.set()

    def recv(self, timeout=None):
        if self.inbox:
            return self.inbox.pop(0)
        threading.Event().wait(0.02)
        raise TimeoutError


def test_notice_body_ensures_one_listener_per_env_and_teardown_stops(
    project, conn, settings, monkeypatch
):
    from trader.features.accounts.ports import AccountsPortImpl

    accounts = [
        make_account("paper-main", "paper", "auto"),
        make_account("real-main", "real", "off"),
    ]
    secrets = Secrets(
        KIS_PAPER_APP_KEY="pk",
        KIS_PAPER_APP_SECRET="ps",
        KIS_REAL_APP_KEY="rk",
        KIS_REAL_APP_SECRET="rs",
        KIS_HTS_ID="myhts",
    )
    port = AccountsPortImpl(accounts, secrets)
    port.policy = lambda c, a: ("off", None) if a.env == "real" else ("auto", None)
    socks: dict[str, SlowConn] = {}

    def connect(url):
        env = "real" if url.endswith(":21000") else "paper"
        socks[env] = SlowConn()
        return socks[env]

    auth = SimpleNamespace(approval_key=lambda: "ak", invalidate_approval=lambda: None)
    monkeypatch.setattr(notice, "_default_connect", connect)
    monkeypatch.setattr(spot_jobs, "_auth_for", lambda creds: auth)
    deps = SimpleNamespace(
        settings=settings,
        conn_factory=db.connect,
        queue=SimpleNamespace(kinds=jobq.kinds_of(discover())),
        now=lambda: datetime(2026, 10, 27, 0, 0, tzinfo=UTC),
        secrets=secrets,
        ports={"accounts": port},
    )
    spec = spot_jobs.NOTICE_LISTENER
    assert (spec.id, spec.kind, spec.trigger, spec.interval_s, spec.gate, spec.start_soon_s) == (
        "notice_listener", "notice_listener", "interval", 30, "none", 5,
    )  # fmt: skip
    assert spec.teardown is spot_jobs.notice_stop and spec.enabled(settings) is False
    try:
        out = spec.body(deps, None)
        assert set(out["listeners"]) == {"paper", "real"}
        assert socks["paper"].subscribed.wait(timeout=5)
        threads = dict(spot_jobs._LISTENERS)
        again = spec.body(deps, None)  # 이미 도는 리스너는 그대로(새로 만들지 않는다)
        assert set(again["listeners"]) == {"paper", "real"}
        assert dict(spot_jobs._LISTENERS) == threads
        assert "real" not in socks  # 실전 계좌가 모두 off → 실전 세션을 쓰지 않는다
    finally:
        spot_jobs.notice_stop(deps)
    assert spot_jobs._LISTENERS == {}
    assert socks["paper"].closed
    assert not [t for t in threading.enumerate() if t.name.startswith("kis-notice-")]
