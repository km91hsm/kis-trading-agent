from pathlib import Path


def test_notify_public_surface():
    import trader.kernel.notify as n

    for name in ("Notifier", "NullNotifier", "TelegramNotifier", "TelegramReceiver", "Update",
                 "parse_command", "make_notifier", "make_receiver", "CommandContext", "poll_once",
                 "messages"):
        assert name in n.__all__ and hasattr(n, name)
    # 주문·승인 문구(M4 Task 6)
    for name in ("DecisionBrief", "opinion_lines", "order", "order_unknown",
                 "bookkeeping_failure", "execution_failure", "approve_hint", "approval_request",
                 "approval_rejected", "approval_tampered", "open_buys_cancelled",
                 "throttle_skip"):
        assert name in n.__all__ and getattr(n, name) is getattr(n.messages, name)


def test_architecture_has_notify_section_and_accounts_readme():
    text = Path("docs/architecture.md").read_text(encoding="utf-8")
    assert "## notify" in text and "notify.enabled" in text and "알려진 차이" in text
    readme = Path("src/trader/features/accounts/README.md").read_text(encoding="utf-8")
    assert "account_modes" in readme and "LIVE" in readme and "알려진 차이" in readme
