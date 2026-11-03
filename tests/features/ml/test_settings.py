"""ml.yaml 섹션 모델 (옛 test_config_ml 1·2·3·7·8·10)."""

import shutil

import pytest
import yaml
from pydantic import ValidationError

from tests.features.ml.helpers import fast_cfg
from trader.features.ml.settings import MlModelsCfg, QuantModelCfg, load_ml, ml_config
from trader.kernel.config import ConfigError
from trader.kernel.platform.bundle import templates_dir

TEMPLATE = templates_dir() / "config" / "ml.yaml"


def test_load_ml_defaults_and_template_yaml(project):
    m = load_ml()  # 파일 없으면 기본값
    assert m.label.horizon_days == 5 and m.label.thresholds.up == 0.02
    assert m.label.thresholds.down == -0.02 and m.label.embargo_days == 5
    assert m.train.walk_forward_folds == 6 and m.models.quant.promote_min_accuracy == 0.40
    shutil.copy(TEMPLATE, project / "config" / "ml.yaml")
    m2 = load_ml()
    assert m2.models.calibration.min_decisions == 60 and m2.models.rl_sizer.seeds == 3
    (project / "config" / "ml.yaml").write_text(
        yaml.safe_dump({"label": {"horizon": 3}}), encoding="utf-8"
    )
    with pytest.raises(ConfigError):
        load_ml()


def test_rl_sizer_defaults_and_mode(project):
    rl = ml_config().models.rl_sizer
    assert rl.enabled is False and rl.seeds == 3 and rl.mode == "shadow"
    assert rl.backend == "auto" and rl.max_weight == 0.25 and rl.min_steps == 300
    with pytest.raises(ValidationError):
        ml_config({"models": {"rl_sizer": {"mode": "live"}}})
    assert load_ml(path=TEMPLATE).models.rl_sizer.mode == "shadow"  # 템플릿과 모델이 맞는다


def test_label_mode_default_absolute_and_relative_allowed():
    assert ml_config().label.mode == "absolute"
    cfg = ml_config({"label": {"mode": "relative_tercile"}})
    assert cfg.label.mode == "relative_tercile"


def test_template_ml_yaml_uses_auto_backends():
    """사용자 결정(2026-10-20): 설정 파일은 auto — lightgbm·PPO 가 import 되면 쓰고, 없으면
    sklearn·cem 으로 돈다. 코드 기본값(sklearn)은 그대로다."""
    m = load_ml(path=TEMPLATE)
    assert m.models.quant.backend == "auto" and m.models.rl_sizer.backend == "auto"
    assert ml_config().models.quant.backend == "sklearn"


def test_template_ml_yaml_has_us_alpha_threshold():
    """사용자 결정(2026-10-20): US 승격 알파 기준은 US 왕복비용(50bp) 0.005."""
    q = load_ml(path=TEMPLATE).models.quant
    assert q.min_alpha_for("US") == 0.005 and q.min_alpha_for("KR") == 0.003


def test_promote_min_alpha_by_market_rejects_unknown_market_key():
    """`{us: 0.005}` 같은 오타가 조용히 무시돼 기본값이 적용되면 안 된다 (리뷰 #9)."""
    with pytest.raises(ValidationError):
        QuantModelCfg.model_validate({"promote_min_alpha_by_market": {"us": 0.005}})
    q = QuantModelCfg.model_validate({"promote_min_alpha_by_market": {"US": 0.005}})
    assert q.min_alpha_for("US") == 0.005


def test_template_ml_yaml_has_every_field():
    models = yaml.safe_load(TEMPLATE.read_text(encoding="utf-8"))["models"]
    assert set(models) == set(MlModelsCfg.model_fields)


def test_fast_cfg_shrinks_boosting():
    assert fast_cfg().models.quant.max_iter == 40
