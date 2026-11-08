"""scalp `/status` 「단타」 절 — 옛 `status._scalp_lines` 이식."""

from tests.features.scalp.conftest import NOW
from trader.features.scalp import status, switch
from trader.features.scalp.feature import FEATURE
from trader.features.scalp.settings import ScalpCfg
from trader.features.scalp.state import save_state
from trader.kernel.notify import commands
from trader.kernel.notify.telegram import NullNotifier
from trader.kernel.registry.types import Feature, StatusSection
from trader.kernel.session.run_store import run_date_for


def _ctx(conn, settings, features=(FEATURE,)):
    return commands.CommandContext(
        conn=conn, settings=settings, state=None, now=lambda: NOW, notifier=NullNotifier(),
        features=list(features),
    )  # fmt: skip


def test_section_is_registered():
    assert FEATURE.status_sections == (status.STATUS_SECTION,)
    assert status.STATUS_SECTION.title == "단타"


def _with_port(ctx, accounts):
    from tests.features.accounts.conftest import make_account
    from trader.features.accounts.ports import AccountsPortImpl
    from trader.kernel.config.secrets import Secrets

    ctx.ports = {"accounts": AccountsPortImpl([make_account(*a) for a in accounts], Secrets())}
    return ctx


def test_off_line(conn, settings):
    off = settings.model_copy(update={"scalp": ScalpCfg()})
    ctx = _with_port(_ctx(conn, off), [("paper-main", "paper", "auto"), ("real-1", "real", "auto")])
    assert status.render(ctx) == ["- 끔 (켜진 계좌 없음)"]


def test_on_line_lists_enabled_accounts(conn, settings):
    from trader.kernel.ledger.control import set_flag

    ctx = _with_port(
        _ctx(conn, settings), [("paper-main", "paper", "auto"), ("real-1", "real", "auto")]
    )
    set_flag(conn, "scalp_enabled:real-1", True)
    # 실전은 LIVE 확인 전이라 스위치만 켜져 있고 실효는 아니다 — 사유를 붙인다
    _, why = switch.account_enabled(
        conn, settings, ctx.ports["accounts"].accounts()[1], ctx.ports["accounts"]
    )
    assert why
    assert status.render(ctx)[0] == f"- 켬: paper-main, real-1(실효 아님: {why})"
    set_flag(conn, "paused", True)
    assert status.render(ctx)[0] == (
        "- 켬: paper-main(실효 아님: 전체 일시정지), real-1(실효 아님: 전체 일시정지)"
    )


def test_on_lines_per_market(conn, settings):
    kr = run_date_for("KR", NOW)
    save_state(None, kr, "KR", {
        "open": [{"ticker": "005930"}], "closed": [{"ticker": "000660"}, {"ticker": "005930"}],
        "done": [], "halted": "일일 손실 한도",
    })  # fmt: skip
    lines = status.render(_with_port(_ctx(conn, settings), [("paper-main", "paper", "auto")]))
    assert lines[0] == "- 켬: paper-main"
    assert lines[1] == "- KR: 보유 1·청산 2, 중단: 일일 손실 한도"
    assert "- US: 보유 0·청산 0" in lines and len(lines) == len(settings.markets) + 1


def test_render_failure_is_one_line_and_status_survives(conn, settings, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("상태 파일 깨짐")

    monkeypatch.setattr(status, "load_state", boom)
    lines = status.render(_with_port(_ctx(conn, settings), [("paper-main", "paper", "auto")]))
    assert lines == ["- 단타 조회 실패: RuntimeError: 상태 파일 깨짐"]
    other = Feature(
        id="zz", tier="service", status_sections=(StatusSection("기타", lambda c: ["- 다른 절"]),)
    )
    ctx = _with_port(
        _ctx(conn, settings, features=(FEATURE, other)), [("paper-main", "paper", "auto")]
    )
    msg = commands.status_message(ctx)
    assert "단타 조회 실패" in msg and "- 다른 절" in msg
