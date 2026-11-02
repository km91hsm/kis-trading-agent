from datetime import UTC, datetime
from pathlib import Path

from trader.kernel.lane.types import LANES, LaneContext, WorkItem
from trader.kernel.registry import contracts


def test_lane_context_fields_and_lanes(tmp_path: Path):
    assert LANES == frozenset({"spot", "fo", "scalp"})
    assert contracts.LANES is LANES
    ctx = LaneContext(
        settings=None, conn=None, state=tmp_path, now=lambda: datetime.now(UTC),
        dry_run=True, notifier=None, ports={"a": 1},
    )  # fmt: skip
    assert ctx.ext == {} and ctx.ports["a"] == 1 and ctx.dry_run
    assert WorkItem("005930", None).exchange is None
