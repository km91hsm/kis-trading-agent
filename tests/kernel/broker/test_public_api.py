from pathlib import Path


def test_broker_public_surface():
    import trader.kernel.broker as b

    for name in (
        "KisApiError", "KisAuth", "KisClient", "KisMarket", "KisBroker", "KisFoBroker",
        "FakeMarket", "FakeBroker", "FakeFoBroker", "KisCreds", "BrokerAccount", "FoProduct",
        "RateLimitBusy", "SharedRateLimiter", "client_for", "build_market", "build_broker",
        "shared_state_dir", "reset_clients", "run_checks", "failures", "Check",
    ):  # fmt: skip
        assert name in b.__all__ and hasattr(b, name)


def test_architecture_has_broker_section():
    root = Path(__file__).resolve().parents[3]
    assert (root / "pyproject.toml").is_file()
    text = (root / "docs" / "architecture.md").read_text(encoding="utf-8")
    assert "## broker" in text and "TRADER_KIS_SHARED_DIR" in text and "알려진 차이" in text
