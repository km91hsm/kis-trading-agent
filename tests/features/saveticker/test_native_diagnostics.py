import json
from datetime import UTC, datetime

import pytest

from trader.features.saveticker.native import NativeSession


def test_stop_diagnostic_survives_disconnect_and_new_connection(tmp_path):
    client = NativeSession(tmp_path)
    reply = client.handle(
        {
            "id": 1,
            "type": "diagnostics",
            "category": "recheck",
            "reason": "recovery_timeout",
            "reload_count": 1,
            "url": "PRIVATE_SENTINEL",
        }
    )
    assert reply["ok"] is True
    path = tmp_path / "runtime/extension-diagnostics.json"
    saved = json.loads(path.read_text())
    assert saved["category"] == "recheck"
    assert saved["reason"] == "recovery_timeout"
    assert saved["reload_count"] == 1
    assert set(saved) == {"category", "reason", "reload_count", "terminal", "recorded_at"}
    assert datetime.fromisoformat(saved["recorded_at"]).tzinfo == UTC
    assert "PRIVATE_SENTINEL" not in path.read_text()
    client.disconnect()
    NativeSession(tmp_path).handle({"id": 2, "type": "hello", "protocol": 1})
    assert json.loads(path.read_text()) == saved


@pytest.mark.parametrize(
    "update",
    [
        {"category": "PRIVATE_SENTINEL"},
        {"reason": "PRIVATE_SENTINEL"},
        {"reason": "recovery_timeout"},
        {"reload_count": -1},
        {"reload_count": True},
        {"reload_count": 1.5},
        {"reload_count": 1000001},
    ],
)
def test_invalid_diagnostic_does_not_replace_last_valid_result(tmp_path, update):
    client = NativeSession(tmp_path)
    message = {
        "id": 1,
        "type": "diagnostics",
        "category": "html_trial",
        "reason": "trial_complete",
        "reload_count": 8,
    }
    assert client.handle(message)["ok"] is True
    path = tmp_path / "runtime/extension-diagnostics.json"
    before = path.read_bytes()
    reply = client.handle({**message, **update})
    assert reply["ok"] is False
    assert reply["error"] == "invalid_message"
    assert path.read_bytes() == before


@pytest.mark.parametrize("terminal", [True, False])
def test_diagnostic_terminal_flag_is_saved(tmp_path, terminal):
    client = NativeSession(tmp_path)
    message = {
        "id": 1,
        "type": "diagnostics",
        "category": "recheck",
        "reason": "page_unverified",
        "reload_count": 2,
        "terminal": terminal,
    }
    assert client.handle(message)["ok"] is True
    saved = json.loads((tmp_path / "runtime/extension-diagnostics.json").read_text())
    assert saved["terminal"] is terminal


def test_old_extension_without_terminal_is_saved_as_terminal(tmp_path):
    client = NativeSession(tmp_path)
    message = {
        "id": 1,
        "type": "diagnostics",
        "category": "recheck",
        "reason": "page_unverified",
        "reload_count": 2,
    }
    assert client.handle(message)["ok"] is True
    saved = json.loads((tmp_path / "runtime/extension-diagnostics.json").read_text())
    assert saved["terminal"] is True


@pytest.mark.parametrize("bad", ["false", 0, 1, None])
def test_non_boolean_terminal_is_rejected(tmp_path, bad):
    client = NativeSession(tmp_path)
    reply = client.handle(
        {
            "id": 1,
            "type": "diagnostics",
            "category": "recheck",
            "reason": "page_unverified",
            "reload_count": 2,
            "terminal": bad,
        }
    )
    assert reply["ok"] is False and reply["error"] == "invalid_message"
