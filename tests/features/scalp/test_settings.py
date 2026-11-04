"""scalp 설정 절."""

import pytest
from pydantic import ValidationError

from trader.features.scalp.settings import ScalpCfg
from trader.kernel.cli.boot import build_registry
from trader.kernel.config.loader import load
from trader.kernel.platform.bundle import templates_dir
from trader.kernel.registry import discover


def test_scalp_cfg_defaults_and_bounds():
    cfg = ScalpCfg()
    assert (cfg.enabled, cfg.max_weight_pct, cfg.max_positions, cfg.max_universe) == (
        False, 0.05, 2, 10,
    )  # fmt: skip
    assert (cfg.take_profit_pct, cfg.stop_pct, cfg.max_hold_min, cfg.daily_stop_pct) == (
        0.015, 0.01, 60, 0.01,
    )  # fmt: skip
    assert (cfg.judge, cfg.judge_daily_cap, cfg.judge_timeout_s, cfg.judge_max_drift_pct) == (
        False, 60, 45.0, 0.003,
    )  # fmt: skip
    assert (
        cfg.judge_hold_every_min,
        cfg.digest_every_min,
        cfg.warmup_min,
        cfg.close_before_min,
        cfg.volume_mult,
    ) == (5, 30, 15, 20, 3.0)
    with pytest.raises(ValidationError):
        ScalpCfg(max_weight_pct=0.3)
    with pytest.raises(ValidationError):
        ScalpCfg(unknown=1)


def test_template_scalp_block_matches_model():
    reg = build_registry(discover())
    st = load("settings", reg, path=templates_dir() / "config" / "settings.yaml")
    assert st.scalp == ScalpCfg()
