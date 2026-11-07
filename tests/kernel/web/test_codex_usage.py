"""구독 관측과 이 프로그램의 Codex 호출량은 토론 설정과 독립이다."""

import json
import sys
from concurrent.futures import ThreadPoolExecutor

import pytest

from tests.kernel.web.conftest import ledger_deps, make_client


def _fake_codex(project, response):
    binary = project / "fake-codex"
    log = project / "requests.jsonl"
    binary.write_text(
        f"#!{sys.executable}\n"
        "import json, os, sys\n"
        f"log = {str(log)!r}\n"
        "for line in sys.stdin:\n"
        "    message = json.loads(line)\n"
        "    with open(log, 'a') as out:\n"
        "        out.write(json.dumps({'method': message['method'], 'cwd': os.getcwd(), "
        "'secret_inherited': 'OPENAI_API_KEY' in os.environ}) + '\\n')\n"
        "    if message['method'] == 'initialize':\n"
        "        print(json.dumps({'id': message['id'], 'result': {}}), flush=True)\n"
        "    elif message['method'] == 'account/rateLimits/read':\n"
        f"        print(json.dumps({{'id': message['id'], **{response!r}}}), flush=True)\n",
        encoding="utf-8",
    )
    binary.chmod(0o700)
    return binary, log


def test_codex_usage_is_visible_when_debate_is_off_and_cli_missing(project):
    deps = ledger_deps(project)
    deps.settings.llm.codex_debate.bin = str(project / "missing-codex")
    response = make_client(deps).get("/api/system/codex-usage")
    assert response.status_code == 200
    data = response.json()
    assert data["enabled"] is False
    assert data["usage"]["calls"] == 0
    assert data["usage"]["daily_call_cap"] == 80
    assert data["rate_limits"]["status"] == "unavailable"
    assert data["rate_limits"]["reason"] == "cli_missing"
    assert data["rate_limits"]["five_hour"] is None
    assert data["rate_limits"]["seven_day"] is None


def test_weekly_primary_is_weekly_and_concurrent_read_is_single_cached_rpc(project, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "never-inherit")
    binary, log = _fake_codex(
        project,
        {
            "result": {
                "accountId": "never-return-account",
                "rateLimits": {
                    "primary": {
                        "usedPercent": 89,
                        "windowDurationMins": 10080,
                        "resetsAt": 1794137425,
                    },
                    "secondary": None,
                },
            }
        },
    )
    deps = ledger_deps(project)
    deps.settings.llm.codex_debate.bin = str(binary)
    client = make_client(deps)
    with ThreadPoolExecutor(max_workers=3) as pool:
        outputs = list(pool.map(lambda _: client.get("/api/system/codex-usage").json(), range(3)))
    assert all(out.get("rate_limits", {}).get("status") == "ready" for out in outputs)
    limits = outputs[0]["rate_limits"]
    assert limits["five_hour"] is None
    assert limits["seven_day"]["used_pct"] == 89
    assert limits["seven_day"]["resets_at"] == "2026-11-08T11:30:25+00:00"
    assert "never-return-account" not in json.dumps(outputs)
    messages = [json.loads(line) for line in log.read_text().splitlines()]
    assert [m["method"] for m in messages] == [
        "initialize",
        "initialized",
        "account/rateLimits/read",
    ]
    assert all(not m["secret_inherited"] for m in messages)
    assert all(m["cwd"] != str(project) for m in messages)


def test_auth_failure_is_sanitized_and_negatively_cached(project):
    binary, log = _fake_codex(
        project,
        {
            "error": {
                "code": -32000,
                "message": "authentication required: secret@example.com private-token",
            }
        },
    )
    deps = ledger_deps(project)
    deps.settings.llm.codex_debate.bin = str(binary)
    client = make_client(deps)
    first = client.get("/api/system/codex-usage")
    second = client.get("/api/system/codex-usage")
    assert first.status_code == second.status_code == 200
    limits = first.json()["rate_limits"]
    assert limits["status"] == "unavailable"
    assert limits["reason"] == "authentication_required"
    assert limits["five_hour"] is limits["seven_day"] is None
    assert "private-token" not in first.text and "secret@example.com" not in first.text
    assert len(log.read_text().splitlines()) == 3


def test_malformed_window_does_not_crash_or_claim_zero_usage(project):
    binary, _ = _fake_codex(
        project,
        {
            "result": {
                "rateLimits": {
                    "primary": {"usedPercent": 12, "windowDurationMins": [], "resetsAt": None},
                }
            }
        },
    )
    deps = ledger_deps(project)
    deps.settings.llm.codex_debate.bin = str(binary)
    response = make_client(deps).get("/api/system/codex-usage")
    assert response.status_code == 200
    limits = response.json()["rate_limits"]
    assert limits["status"] == "unavailable"
    assert limits["five_hour"] is limits["seven_day"] is None


def test_cached_success_becomes_stale_when_refresh_fails(project, monkeypatch):
    from datetime import timedelta

    from tests.kernel.web.conftest import NOW
    from trader.kernel.session import codex_usage

    binary, _ = _fake_codex(
        project,
        {
            "result": {
                "rateLimits": {
                    "primary": {"usedPercent": 0, "windowDurationMins": 300, "resetsAt": None},
                    "secondary": {"usedPercent": 34, "windowDurationMins": 10080, "resetsAt": None},
                }
            }
        },
    )
    first = codex_usage.read_limits(str(binary), now=NOW)
    assert first["status"] == "ready" and first["five_hour"]["used_pct"] == 0
    monkeypatch.setattr(codex_usage, "CACHE_TTL_S", 0)
    _fake_codex(project, {"error": {"message": "network unavailable"}})
    stale = codex_usage.read_limits(str(binary), now=NOW + timedelta(minutes=2))
    assert stale["status"] == "stale" and stale["reason"] == "rpc_error"
    assert stale["age_s"] == 120 and stale["seven_day"]["used_pct"] == 34


def test_timeout_terminates_child_and_output_overflow_is_bounded(project, monkeypatch):
    import os

    from tests.kernel.web.conftest import NOW
    from trader.kernel.session import codex_usage

    binary = project / "hanging-codex"
    binary.write_text(f"#!{sys.executable}\nimport time\ntime.sleep(10)\n")
    binary.chmod(0o700)
    children = []
    real_popen = codex_usage.subprocess.Popen

    def start(*args, **kwargs):
        process = real_popen(*args, **kwargs)
        children.append(process)
        return process

    monkeypatch.setattr(codex_usage.subprocess, "Popen", start)
    monkeypatch.setattr(codex_usage, "RPC_TIMEOUT_S", 0.3)
    out = codex_usage.read_limits(str(binary), now=NOW)
    assert out["reason"] == "timeout"
    try:
        os.kill(children[0].pid, 0)
    except ProcessLookupError:
        pass
    else:
        raise AssertionError("timed-out child survived")
    binary = project / "noisy-codex"
    monkeypatch.setattr(codex_usage, "RPC_TIMEOUT_S", 8)
    binary.write_text(
        f"#!{sys.executable}\nimport sys, time\n"
        "sys.stdout.write('x' * 300000); sys.stdout.flush()\ntime.sleep(10)\n"
    )
    binary.chmod(0o700)
    out = codex_usage.read_limits(str(binary), now=NOW)
    assert out["reason"] == "invalid_response"


@pytest.mark.parametrize(
    "snapshot,want_5h,want_7d",
    [
        (
            {
                "rateLimits": {
                    "primary": {"usedPercent": 34, "windowDurationMins": 10080},
                    "secondary": {"usedPercent": 12, "windowDurationMins": 300},
                }
            },
            12,
            34,
        ),
        (
            {
                "rateLimits": {
                    "primary": {"usedPercent": True, "windowDurationMins": 300},
                    "secondary": {"usedPercent": float("inf"), "windowDurationMins": 10080},
                }
            },
            None,
            None,
        ),
        (
            {
                "rateLimits": {"primary": {"usedPercent": 99, "windowDurationMins": 300}},
                "rateLimitsByLimitId": {
                    "codex": {"primary": {"usedPercent": 22, "windowDurationMins": 10080}}
                },
            },
            None,
            22,
        ),
        (
            {
                "rateLimits": {"primary": {"usedPercent": 15, "windowDurationMins": 300}},
                "rateLimitsByLimitId": None,
            },
            15,
            None,
        ),
    ],
)
def test_actual_window_duration_and_codex_bucket_choose_limits(
    tmp_path, monkeypatch, snapshot, want_5h, want_7d
):
    from tests.kernel.web.conftest import NOW
    from trader.kernel.session import codex_usage

    monkeypatch.setattr(codex_usage, "find_codex", lambda _: tmp_path / "codex")
    monkeypatch.setattr(codex_usage, "_rpc", lambda _: snapshot)
    limits = codex_usage.read_limits(str(tmp_path), now=NOW)
    assert (limits["five_hour"] or {}).get("used_pct") == want_5h
    assert (limits["seven_day"] or {}).get("used_pct") == want_7d


def test_invalid_reset_timestamp_is_not_displayed_as_epoch(tmp_path, monkeypatch):
    from tests.kernel.web.conftest import NOW
    from trader.kernel.session import codex_usage

    monkeypatch.setattr(codex_usage, "find_codex", lambda _: tmp_path / "codex")
    monkeypatch.setattr(
        codex_usage,
        "_rpc",
        lambda _: {
            "rateLimits": {
                "primary": {"usedPercent": 12, "windowDurationMins": 300, "resetsAt": True},
            }
        },
    )
    assert codex_usage.read_limits(str(tmp_path), now=NOW)["five_hour"]["resets_at"] is None
