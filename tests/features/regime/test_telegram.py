"""regime 텔레그램 `/kill`·`/status` 국면 절.

옛 `tests/notify/test_commands.py::test_telegram_kill_engages_only` +
`test_untrusted_sender_can_only_read` 의 `/kill` 거부 부분 이식. 치환: 커널 디스패처
`commands.poll_once` + `CommandContext.features=discover()`.
해제 안내 문구 단언 `"control killswitch off"` → `"trader regime killswitch off"`(명령 이동,
regime README 알려진 차이 4)."""

from dataclasses import replace
from datetime import UTC, datetime

from trader.features.regime import killswitch
from trader.features.regime.feature import FEATURE
from trader.features.regime.telegram import KILL
from trader.kernel.notify import commands
from trader.kernel.notify.commands import CommandContext
from trader.kernel.notify.telegram import Update
from trader.kernel.registry import discover


class Sent:
    def __init__(self):
        self.msgs = []

    def send(self, text, *, escape=True):
        self.msgs.append(text)
        return True


class Recv:
    last_update_id = None

    def __init__(self, texts, *, trusted=True):
        self.texts, self.trusted = texts, trusted

    def poll(self, offset=None):
        return [
            Update(update_id=i + 1, text=t, chat_id="1", trusted=self.trusted)
            for i, t in enumerate(self.texts)
        ]


def _ctx(conn, settings, sent):
    return CommandContext(
        conn=conn,
        settings=settings,
        state=None,
        now=lambda: datetime.now(UTC),
        notifier=sent,
        features=discover(),
    )


def test_telegram_kill_engages_only(conn, settings):
    sent = Sent()
    ctx = _ctx(conn, settings, sent)
    commands.poll_once(ctx, Recv(["/kill"]))
    assert killswitch.reason(conn) == "텔레그램 /kill"
    msg = sent.msgs[-1]
    assert msg.startswith("[킬스위치]") and "trader regime killswitch off" in msg


def test_untrusted_sender_cannot_kill(conn, settings):
    """2-T2: 그룹 채팅(허용 목록 밖) 구성원은 /kill 을 쓸 수 없다 — 해제는 CLI 로만 되므로."""
    sent = Sent()
    commands.poll_once(_ctx(conn, settings, sent), Recv(["/kill"], trusted=False))
    assert sent.msgs[-1].startswith("[명령 거부]")
    assert killswitch.reason(conn) is None
    assert (KILL.name, KILL.untrusted_ok) == ("kill", False)


def test_status_has_regime_section(conn, settings):
    sec = FEATURE.status_sections[0]
    assert sec.title == "국면"
    ctx = _ctx(conn, settings, Sent())
    lines = sec.render(ctx)
    assert lines[0] == "- KR - · US -"
    assert "KR - · US -" in commands.status_message(ctx)
    # 설정 없는 문맥에서도 /status 가 깨지지 않는다
    assert sec.render(replace(ctx, settings=None)) == []


def test_status_regime_section(conn, settings):
    ctx = _ctx(conn, settings, Sent())
    render = FEATURE.status_sections[0].render
    assert render(ctx) == [
        "- KR - · US -",
        "- 헤지: KR 114800/252670, US SH/SDS 최대 20% · bear 기준 2/3",
        "- 제안 적중률: KR - · US -",
    ]
    hedge = settings.hedge.model_copy(update={"leverage": False, "market_session": False})
    assert render(replace(ctx, settings=settings.model_copy(update={"hedge": hedge}))) == [
        "- KR - · US -",
        "- 헤지: KR 114800, US SH 최대 20% · bear 기준 2/3",
    ]
    off = settings.hedge.model_copy(update={"enabled": False})
    assert render(replace(ctx, settings=settings.model_copy(update={"hedge": off}))) == [
        "- KR - · US -",
        "- 헤지 비활성 (hedge.enabled=false)",
    ]
