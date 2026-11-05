"""ml `/status`·STATUS.md 「ML 모델」 절."""

from datetime import UTC, datetime, timedelta

from trader.features.ml import registry, status
from trader.features.ml.feature import FEATURE
from trader.kernel.notify.commands import CommandContext

NOW = datetime(2026, 11, 5, 1, 0, tzinfo=UTC)


class _Sent:
    def send(self, text, *, escape=True):
        return True


def _ctx(conn, project):
    return CommandContext(
        conn=conn, settings=None, state=project / "state", now=lambda: NOW,
        notifier=_Sent(), features=[FEATURE],
    )  # fmt: skip


def test_status_shows_ml_model(conn, project):
    ctx = _ctx(conn, project)
    assert [s.title for s in FEATURE.status_sections] == ["ML 모델"]
    assert status.model_lines(ctx) == ["- ML 모델 없음"]
    for i, (market, acc) in enumerate((("KR", 0.55), ("US", 0.58), ("KR", 0.61), (None, 0.5))):
        at = datetime(2026, 10, 29, 15, 30, tzinfo=UTC) + timedelta(days=i)  # KST 다음날 00:30
        registry.save(
            "quant", {"m": i}, {"market": market, "metrics": {"accuracy": acc}},
            state=ctx.state, now=lambda at=at: at,
        )  # fmt: skip
    registry.promote("quant", "20261031_153000", state=ctx.state, reason="test")
    assert status.model_lines(ctx) == [
        "- 20261101_153000 전체 정확도 0.5 · 미승격 (2026-11-02)",
        "- 20261031_153000 KR 정확도 0.61 · 승격 (2026-11-01)",
        "- 20261030_153000 US 정확도 0.58 · 미승격 (2026-10-31)",
    ]
