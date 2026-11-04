"""kernel/session/fake: FAKE 세션 공용 헬퍼와 기능 fake 모듈의 재사용."""

import re
from pathlib import Path

import pytest

import trader


def test_fake_call_shape_and_error():
    from trader.kernel.session.fake import FAKE_MODEL, FakeSessionError, ensure_ok, fake_call

    assert fake_call("x") == {
        "agent": "x",
        "model": FAKE_MODEL,
        "session_id": "fake",
        "cost_usd": 0.0,
        "duration_ms": 0,
    }
    ensure_ok({"ok": True})
    with pytest.raises(FakeSessionError, match="boom"):
        ensure_ok({"ok": False, "error": "boom"})


def test_fake_model_is_calls_fake_model():
    from trader.kernel.session import calls
    from trader.kernel.session.fake import FAKE_MODEL

    assert FAKE_MODEL is calls.FAKE_MODEL


def test_features_reuse_kernel_fake_helpers():
    root = Path(trader.__file__).parent / "features"
    for name in ("spot", "regime", "scan", "evaluation"):
        src = (root / name / "fake.py").read_text(encoding="utf-8")
        assert not re.search(r"^FAKE_MODEL\s*=", src, re.M), name
        assert not re.search(r"^def _call\(", src, re.M), name
        assert not re.search(r"^class FakeSessionError", src, re.M), name
        assert not re.search(r"^def _ok\(", src, re.M), name
        assert "trader.kernel.session.fake" in src, name
