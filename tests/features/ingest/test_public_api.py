"""ingest 공개면: __all__ 의 모든 이름이 import 된다."""

from trader.features.ingest import api


def test_all_names_importable():
    assert len(api.__all__) == len(set(api.__all__))
    for name in api.__all__:
        assert getattr(api, name) is not None, name
