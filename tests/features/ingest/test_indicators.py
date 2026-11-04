"""ingest `indicators` 는 커널 `broker.indicators` 의 재노출이다(본문 테스트는
`tests/kernel/broker/test_indicators.py`)."""

from trader.features.ingest import indicators as ingest_ind
from trader.kernel.broker import indicators as kernel_ind


def test_ingest_indicators_reexports_kernel():
    for name in ("COLUMNS", "latest", "indicator_frame", "to_frame"):
        assert getattr(ingest_ind, name) is getattr(kernel_ind, name)
