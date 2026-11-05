from datetime import UTC, datetime

import yaml

from trader.features.accounts import modes
from trader.features.accounts.settings import load_accounts
from trader.kernel.config.secrets import Secrets
from trader.kernel.notify import commands
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

    def __init__(self, texts):
        self.texts = texts

    def poll(self, offset=None):
        return [Update(update_id=i + 1, text=t, chat_id="1") for i, t in enumerate(self.texts)]


def _write(project, extra=()):
    data = {
        "accounts": [
            {"id": "paper-main", "env": "paper", "cano": "0", "markets": ["KR"], "mode": "auto"},
            {"id": "real-main", "env": "real", "cano": "1", "markets": ["KR"], "mode": "off"},
            *extra,
        ]
    }
    (project / "config" / "accounts.yaml").write_text(yaml.safe_dump(data), encoding="utf-8")


def _ctx(conn, sent):
    return commands.CommandContext(
        conn=conn,
        settings=None,
        state=None,
        now=lambda: datetime.now(UTC),
        notifier=sent,
        features=discover(),
    )


def _acct(id_):
    return next(a for a in load_accounts(secrets=Secrets()) if a.id == id_)


def test_telegram_pause_resume_mode(project, conn):
    _write(project)
    sent = Sent()
    ctx = _ctx(conn, sent)
    r = commands.poll_once(ctx, Recv(["/pause"]))
    assert r["handled"] == 1 and modes.is_paused(conn)
    commands.poll_once(ctx, Recv(["/resume"]))
    assert not modes.is_paused(conn)
    commands.poll_once(ctx, Recv(["/mode paper-main approve"]))
    assert modes.get_account_mode(conn, _acct("paper-main")) == "approve"
    rows = conn.execute("SELECT key, channel, actor FROM control_log ORDER BY id").fetchall()
    assert [r["key"] for r in rows] == ["paused", "paused", "account_mode:paper-main"]
    assert {(r["channel"], r["actor"]) for r in rows} == {("telegram", "1")}
    commands.poll_once(ctx, Recv(["/mode nope auto", "/mode paper-main fast"]))
    assert any("사용법" in m or "알 수 없는" in m for m in sent.msgs[-2:])


def test_real_auto_via_telegram_rejected(project, conn):
    _write(project)
    sent = Sent()
    ctx = _ctx(conn, sent)
    commands.poll_once(ctx, Recv(["/mode real-main auto"]))
    assert modes.get_account_mode(conn, _acct("real-main")) == "off"
    assert "거부" in sent.msgs[-1] and "accounts mode" in sent.msgs[-1]
    commands.poll_once(ctx, Recv(["/mode real-main approve"]))
    assert modes.get_account_mode(conn, _acct("real-main")) == "off" and "거부" in sent.msgs[-1]
    modes.set_account_mode(conn, _acct("real-main"), "approve")
    commands.poll_once(ctx, Recv(["/mode real-main off"]))
    assert modes.get_account_mode(conn, _acct("real-main")) == "off"


def test_status_shows_effective_mode_for_unconfirmed_real_auto(project, conn):
    """실전 auto + 확인 기록 없음 → 실효 off(`effective_mode`) — 설정→실효(사유) 로 보인다."""
    _write(project)
    sent = Sent()
    ctx = _ctx(conn, sent)
    conn.execute("INSERT INTO account_modes VALUES ('real-main','auto',NULL,'t')")
    conn.commit()
    commands.poll_once(ctx, Recv(["/status"]))
    msg = sent.msgs[-1]
    assert "- 실행 중" in msg
    assert "- paper-main · paper · KR · auto" in msg
    assert "- real-main · real · KR · auto→off(LIVE 미확인)" in msg


def test_mode_and_status_reach_fo_accounts(project, conn):
    _write(
        project,
        extra=[
            {
                "id": "paper-fo",
                "env": "paper",
                "cano": "0",
                "acnt_prdt_cd": "03",
                "markets": ["KR"],
                "mode": "auto",
                "asset": "fo",
            },
            {
                "id": "real-fo",
                "env": "real",
                "cano": "0",
                "acnt_prdt_cd": "03",
                "markets": ["KR"],
                "mode": "approve",
                "asset": "fo",
            },
        ],
    )
    sent = Sent()
    ctx = _ctx(conn, sent)
    commands.poll_once(ctx, Recv(["/mode paper-fo off"]))
    assert (
        modes.get_account_mode(conn, _acct("paper-fo")) == "off"
        and "알 수 없는" not in sent.msgs[-1]
    )
    commands.poll_once(ctx, Recv(["/status"]))
    msg = sent.msgs[-1]
    assert "- paper-fo · paper · KR · off" in msg
    assert "- real-fo · real · KR · approve→off(실전 파생)" in msg
