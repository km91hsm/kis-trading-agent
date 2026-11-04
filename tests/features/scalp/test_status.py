"""scalp `/status` 「단타(모의 전용)」 절 — 옛 `status._scalp_lines` 이식."""

from tests.features.scalp.conftest import NOW
from trader.features.scalp import status
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
    assert status.STATUS_SECTION.title == "단타(모의 전용)"


def test_off_line(conn, settings):
    off = settings.model_copy(update={"scalp": ScalpCfg()})
    assert status.render(_ctx(conn, off)) == ["- 끔 (scalp.enabled=false)"]


def test_on_lines_per_market(conn, settings):
    kr = run_date_for("KR", NOW)
    save_state(None, kr, "KR", {
        "open": [{"ticker": "005930"}], "closed": [{"ticker": "000660"}, {"ticker": "005930"}],
        "done": [], "halted": "일일 손실 한도",
    })  # fmt: skip
    lines = status.render(_ctx(conn, settings))
    assert lines[0] == "- KR: 보유 1·청산 2, 중단: 일일 손실 한도"
    assert "- US: 보유 0·청산 0" in lines and len(lines) == len(settings.markets)


def test_render_failure_is_one_line_and_status_survives(conn, settings, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("상태 파일 깨짐")

    monkeypatch.setattr(status, "load_state", boom)
    lines = status.render(_ctx(conn, settings))
    assert lines == ["- 단타 조회 실패: RuntimeError: 상태 파일 깨짐"]
    other = Feature(
        id="zz", tier="service", status_sections=(StatusSection("기타", lambda c: ["- 다른 절"]),)
    )
    msg = commands.status_message(_ctx(conn, settings, features=(FEATURE, other)))
    assert "단타 조회 실패" in msg and "- 다른 절" in msg
