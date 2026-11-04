"""화면 실시간 시세 허브: 화면이 요청한 국내 종목만 웹소켓 하나로 구독(최근 요청 순, 41칸 중 40),
요청이 끊기면 해제, 핑퐁 응답, 접속키 거절이면 새 키로 재접속, 장 밖·연결 실패는 조용히.

옛 `tests/web/test_live_quotes.py` 18건 이식(이름 유지; `blocked` → `busy`, `asof` → `at`,
`status()["yield"]` → `yield_reason`) + 세션 양보 플래그 2건."""

import json
from datetime import timedelta

from tests.features.quotes.conftest import NOW, FakeConn, make_hub
from tests.kernel.broker.helpers_ws import book_values, frame, index_values, trade_values
from trader.features.quotes import hub as hub_mod
from trader.kernel.ledger import control, db
from trader.kernel.registry import discover
from trader.kernel.registry.contracts import all_migrations


def test_subscribes_requested_and_serves_ticks():
    hub, clock, made, _ = make_hub()
    assert hub.request(["005930", "000660"]) == {}  # 아직 틱 없음 → 호출부가 REST 로
    hub.step()
    conn = made[0]
    assert sorted(conn.subs()) == [("1", "000660"), ("1", "005930")]
    assert all(m["header"]["approval_key"] == "ak-1" for m in conn.sent)
    conn.inbox.append(frame(trade_values("005930", 71600)))
    hub.step()
    got = hub.request(["005930", "000660"])
    assert got == {
        "005930": {
            "price": "71600",
            "change_pct": 0.0125,
            "at": NOW.isoformat(),
            "stale": False,
            "source": "ws",
        }
    }
    assert hub.status()["connected"] and hub.status()["subscribed"] == 2


def test_lease_expiry_unsubscribes_and_drops_tick():
    hub, clock, made, _ = make_hub()
    hub.request(["005930", "000660"])
    hub.step()
    made[0].inbox.append(frame(trade_values("005930", 71600)))
    hub.step()
    clock[0] = 20.0
    hub.request(["005930"])  # 000660 은 화면에서 빠졌다
    clock[0] = 31.0
    hub.step()
    assert ("2", "000660") in made[0].subs()
    made[0].inbox.append(frame(trade_values("005930", 71700)))
    hub.step()
    assert hub.request(["005930", "000660"]).keys() == {"005930"}


def test_capacity_keeps_most_recent_37():
    """41칸 중 40(max_tickers) = 지수 2 + 호가 1 + 체결가 37, 여유 1."""
    hub, clock, made, _ = make_hub()
    old = [f"{i:06d}" for i in range(1, 11)]
    new = [f"{i:06d}" for i in range(100, 135)]
    hub.request(old)
    clock[0] = 1.0
    hub.request(new)
    hub.step()
    subscribed = {t for op, t in made[0].subs() if op == "1"}
    assert len(subscribed) == 37 and set(new[-30:]) <= subscribed


def test_pingpong_echoed():
    hub, _, made, _ = make_hub()
    hub.request(["005930"])
    hub.step()
    ping = json.dumps({"header": {"tr_id": "PINGPONG", "datetime": "20261027100000"}})
    made[0].inbox.append(ping)
    hub.step()
    assert made[0].sent[-1] == json.loads(ping)


def test_approval_rejected_reconnects_with_new_key():
    hub, clock, made, invalidated = make_hub(conns=[FakeConn(), FakeConn()])
    hub.request(["005930"])
    hub.step()
    made[0].inbox.append(
        json.dumps(
            {
                "header": {"tr_id": "H0STCNT0", "tr_key": "005930"},
                "body": {"rt_cd": "1", "msg1": "invalid approval : NOT FOUND"},
            }
        )
    )
    hub.step()
    assert invalidated == [1] and made[0].closed and not hub.status()["connected"]
    clock[0] = 5.0
    hub.step()
    assert len(made) == 2 and made[1].sent[0]["header"]["approval_key"] == "ak-2"


def test_rejected_ticker_not_requested_again():
    hub, _, made, _ = make_hub()
    hub.request(["999999", "005930"])
    hub.step()
    made[0].inbox.append(
        json.dumps(
            {
                "header": {"tr_id": "H0STCNT0", "tr_key": "999999"},
                "body": {"rt_cd": "1", "msg1": "invalid tr_key"},
            }
        )
    )
    hub.step()
    hub.step()
    assert made[0].subs().count(("1", "999999")) == 1 and hub.status()["subscribed"] == 1


def test_connect_failure_backs_off_without_raising():
    hub, clock, made, _ = make_hub(conns=[OSError("x"), FakeConn()])
    hub.request(["005930"])
    hub.step()
    assert not made and not hub.status()["connected"]
    hub.step()  # 백오프 중이라 다시 시도하지 않는다
    assert not made
    clock[0] = 2.0
    hub.step()
    assert made and hub.status()["connected"]


def test_closed_market_never_connects():
    hub, _, made, _ = make_hub(is_open=False)
    assert hub.request(["005930"]) == {}
    hub.step()
    assert not made


def test_stale_tick_falls_back_to_rest():
    """체결이 멈추거나 끊김을 아직 모르면 틱이 낡는다 → 10초 넘으면 돌려주지 않아 REST 가 채운다."""
    hub, clock, made, _ = make_hub()
    hub.request(["005930"])
    hub.step()
    made[0].inbox.append(frame(trade_values("005930", 71600)))
    hub.step()
    clock[0] = 9.0
    assert "005930" in hub.request(["005930"])
    clock[0] = 11.0
    assert hub.request(["005930"]) == {}


def test_release_rejected_reconnects_to_resync():
    """해제가 거절되면 서버에 구독이 남아 41칸이 어긋난다 → 다시 접속해 새로 맞춘다."""
    hub, clock, made, _ = make_hub(conns=[FakeConn(), FakeConn()])
    hub.request(["005930", "000660"])
    hub.step()
    clock[0] = 31.0
    hub.request(["005930"])
    hub.step()  # 000660 해제 요청
    made[0].inbox.append(
        json.dumps(
            {
                "header": {"tr_id": "H0STCNT0", "tr_key": "000660"},
                "body": {"rt_cd": "1", "msg1": "unsubscribe error"},
            }
        )
    )
    hub.step()
    assert made[0].closed and not hub.status()["connected"]
    clock[0] = 40.0
    hub.request(["005930"])
    hub.step()
    assert made[1].subs() == [("1", "005930")]


def test_backoff_grows_when_connection_drops_right_away():
    class Dropping(FakeConn):
        def recv(self, timeout=None):
            raise ConnectionError("끊김")

    hub, clock, made, _ = make_hub(conns=[Dropping(), Dropping(), Dropping(), FakeConn()])
    hub.request(["005930"])
    retries = []
    for t in range(0, 20):
        clock[0] = float(t)
        hub.request(["005930"])
        before = len(made)
        hub.step()
        if len(made) > before:
            retries.append(t)
    assert retries[:4] == [0, 1, 3, 7]  # 1·2·4초로 늘어난다(접속만 되고 바로 끊겨도)


def test_thread_hands_off_request_arriving_while_exiting(monkeypatch):
    """유휴 종료를 정한 직후 들어온 요청은 새 스레드가 이어받는다."""
    import threading

    hub, clock, made, _ = make_hub(conns=[FakeConn(), FakeConn()])
    hub._autostart = True
    hub._idle_s = 0.0
    started = []
    real_start = threading.Thread.start

    def start(self):
        started.append(self.name)
        real_start(self)

    monkeypatch.setattr(threading.Thread, "start", start)
    clock[0] = 0.0
    hub.request(["005930"])  # 스레드 시작 → 곧 유휴로 끝나지만 마지막 요청이 결정 뒤면 재시작
    for _ in range(50):
        if hub._thread is None:
            break
        threading.Event().wait(0.02)
    hub.stop()
    assert started and hub._thread is None


def test_stop_prevents_restart_and_closes():
    hub, clock, made, _ = make_hub()
    hub.request(["005930"])
    hub.step()
    hub.stop()
    assert made[0].closed and not hub.status()["connected"]
    hub._autostart = True
    hub.request(["005930"])
    assert hub._thread is None  # 내려간 앱에서 다시 띄우지 않는다


def test_repeated_approval_rejection_backs_off():
    """접속키 거절이 매번 오면 재발급·재접속 간격이 1·2·4초로 늘어난다(1초 반복 금지)."""

    def rejecting():
        c = FakeConn()
        c.inbox.append(
            json.dumps(
                {
                    "header": {"tr_id": "H0STCNT0", "tr_key": "005930"},
                    "body": {"rt_cd": "1", "msg1": "invalid approval : NOT FOUND"},
                }
            )
        )
        return c

    hub, clock, made, invalidated = make_hub(
        conns=[rejecting() for _ in range(5)], keys=[f"ak-{i}" for i in range(10)]
    )
    connects = []
    for t in range(0, 12):
        clock[0] = float(t)
        hub.request(["005930"])
        before = len(made)
        hub.step()
        if len(made) > before:
            connects.append(t)
        hub.step()  # 거절 수신
    assert connects[:4] == [0, 1, 3, 7]


def test_indices_and_book_leases():
    """상단 바 지수(KOSPI·KOSDAQ)와 차트 호가 10단도 같은 연결·같은 임차로."""
    hub, clock, made, _ = make_hub()
    assert hub.request_indices() == {} and hub.request_book("005930") is None
    hub.step()
    subs = made[0].sent
    trs = sorted((m["body"]["input"]["tr_id"], m["body"]["input"]["tr_key"]) for m in subs)
    assert trs == [("H0STASP0", "005930"), ("H0UPCNT0", "0001"), ("H0UPCNT0", "1001")]
    made[0].inbox.append("0|H0UPCNT0|001|" + "^".join(index_values("0001", "2650.12", "5", "0.85")))
    made[0].inbox.append("0|H0STASP0|001|" + "^".join(book_values("005930")))
    hub.step()
    hub.step()
    idx = hub.request_indices()
    assert idx["KOSPI"]["price"] == "2650.12" and idx["KOSPI"]["change_pct"] == -0.0085
    book = hub.request_book("005930")
    assert len(book["asks"]) == 10 and book["asks"][0] == ["71100", "10"]
    assert book["total_bid"] == "2000"
    # 호가 임차는 10초 — 차트를 떠나면 칸이 돌아온다
    clock[0] = 11.0
    hub.request_indices()
    hub.step()
    assert ("2", "005930") in made[0].subs()


def test_blocked_hub_does_not_connect_and_yields():
    """Codex(PR #46): 실전 체결통보가 앱키 세션을 쓰는 동안 허브는 붙지 않는다(화면은 REST)."""
    busy = [True]
    hub, clock, made, _ = make_hub(busy=lambda: busy[0])
    assert hub.request(["005930"]) == {}
    busy[0] = False
    hub.request(["005930"])
    hub.step()
    assert len(made) == 1


def test_appkey_in_use_drops_with_backoff():
    c1, c2 = FakeConn(), FakeConn()
    hub, clock, made, invalidated = make_hub(conns=[c1, c2])
    hub.request(["005930"])
    hub.step()
    c1.inbox.append(
        json.dumps(
            {
                "header": {"tr_id": "H0STCNT0", "tr_key": "005930"},
                "body": {"rt_cd": "1", "msg1": "ALREADY IN USE appkey"},
            }
        )
    )
    hub.step()
    assert c1.closed and invalidated == []
    hub.step()
    assert len(made) == 1  # 백오프 중


def test_status_reports_yield_reason():
    """2-P13: 허브가 붙지 않는 이유를 상태로 — 체결통보 리스너가 앱키 세션을 쓰는 중
    (ws_session_busy), 다른 연결이 앱키를 쥐고 있어 백오프 중(appkey_in_use)."""
    busy = [True]
    hub, clock, made, _ = make_hub(conns=[FakeConn(), FakeConn()], busy=lambda: busy[0])
    assert hub.status()["yield_reason"] == "ws_session_busy"
    busy[0] = False
    assert hub.status()["yield_reason"] is None
    c1 = FakeConn()
    hub2, clock2, _, _ = make_hub(conns=[c1, FakeConn()])
    hub2.request(["005930"])
    hub2.step()
    c1.inbox.append(
        json.dumps(
            {
                "header": {"tr_id": "H0STCNT0", "tr_key": "005930"},
                "body": {"rt_cd": "1", "msg1": "ALREADY IN USE appkey"},
            }
        )
    )
    hub2.step()
    assert hub2.status()["yield_reason"] == "appkey_in_use"


def test_hub_yields_to_ws_session_busy_flag():
    """busy 가 참인 동안 step 은 연결을 시도하지 않고(붙어 있으면 놓는다) 사유를 보인다 —
    거짓으로 바뀌면 다음 step 에 접속한다."""
    busy = [False]
    hub, clock, made, _ = make_hub(conns=[FakeConn(), FakeConn()], busy=lambda: busy[0])
    hub.request(["005930"])
    hub.step()
    assert len(made) == 1 and hub.status()["connected"]
    busy[0] = True
    for _ in range(3):
        hub.step()
    assert made[0].closed and len(made) == 1 and not hub.status()["connected"]
    assert hub.status()["yield_reason"] == "ws_session_busy"
    busy[0] = False
    clock[0] = 1.0
    hub.request(["005930"])
    hub.step()
    assert len(made) == 2 and hub.status()["connected"]
    assert hub.status()["yield_reason"] is None


def test_session_busy_reads_control_flag_until(tmp_path):
    """`busy()` 기본 구현: control `ws_session_busy:real` 의 until 이 지금보다 미래면 참."""
    path = tmp_path / "t.db"
    conn = db.connect(path)
    db.init_schema(conn, all_migrations(discover()))
    now = [NOW]
    busy = hub_mod.session_busy(lambda: db.connect(path), lambda: now[0], ttl_s=0.0)
    assert busy() is False  # 플래그 없음
    until = (NOW + timedelta(seconds=90)).isoformat()
    control.set_flag(conn, "ws_session_busy:real", {"until": until})
    assert busy() is True
    now[0] = NOW + timedelta(seconds=91)
    assert busy() is False  # 리스너가 죽어 갱신이 멈췄다 → 만료
    control.delete_flag(conn, "ws_session_busy:real")
    now[0] = NOW
    assert busy() is False
    conn.close()
