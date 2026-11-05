"""외부 감시: 주의 이상이면 알림, 1시간 재알림, 격상 즉시, 데몬 생존 시 새 경고 키만."""

from datetime import UTC, datetime, timedelta

from trader.kernel.ops import watchdog

NOW = datetime(2026, 10, 23, 1, 0, tzinfo=UTC)


class Notes:
    def __init__(self):
        self.sent = []

    def send(self, text, *, escape=True):
        self.sent.append(text)
        return True


def test_run_once_notifies_on_dead_daemon_with_throttle(make_ctx, project):
    n = Notes()
    state = project / "state"
    out = watchdog.run_once(make_ctx(now=NOW), n)
    assert out["level"] == "critical" and out["notified"] is True
    assert n.sent[0].startswith("[감시] 데몬이 꺼져")
    # 10분 뒤: 같은 단계·1시간 안 → 재알림 없음
    out2 = watchdog.run_once(make_ctx(now=NOW + timedelta(minutes=10)), n)
    assert out2["notified"] is False and len(n.sent) == 1
    # 61분 뒤: 재알림
    out3 = watchdog.run_once(make_ctx(now=NOW + timedelta(minutes=61)), n)
    assert out3["notified"] is True and len(n.sent) == 2
    assert (state / watchdog.FILE_NAME).exists()
    rec = watchdog.load(state)
    assert rec["last_level"] == "critical" and rec["last_notified_at"]
    assert "jobs.daemon" in rec["keys"]
    assert rec["last_run_at"] == (NOW + timedelta(minutes=61)).isoformat()


def test_should_notify_rules():
    assert not watchdog.should_notify({}, "watch", NOW)
    assert watchdog.should_notify({}, "warning", NOW)
    prev = {"last_level": "warning", "last_notified_at": NOW.isoformat()}
    assert not watchdog.should_notify(prev, "warning", NOW + timedelta(minutes=30))
    assert watchdog.should_notify(prev, "critical", NOW + timedelta(minutes=1))  # 격상은 즉시
    assert watchdog.should_notify(prev, "warning", NOW + timedelta(hours=1))


def test_alive_daemon_only_new_keys_notify():
    """데몬이 살아 있으면 오래 가는 경고로 1시간마다 울리지 않고 새 경고 키에만."""
    assert not watchdog.should_notify({}, "warning", NOW, daemon_down=False, new_keys=False)
    assert watchdog.should_notify({}, "warning", NOW, daemon_down=False, new_keys=True)
    assert watchdog.should_notify({}, "critical", NOW, daemon_down=True)
