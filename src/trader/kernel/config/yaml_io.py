"""yaml 파일 읽기. 모든 실패는 ConfigError 로 통일한다."""

from __future__ import annotations

from pathlib import Path

import yaml

from trader.kernel.config.errors import ConfigError


def read_yaml(path: Path) -> dict:
    """최상위 매핑을 읽는다. 파일이 없거나 비어 있으면 {}."""
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return {}
    except UnicodeDecodeError as e:
        raise ConfigError(f"{path.name} 은 UTF-8 로 저장해야 합니다 (cp949 등 감지): {e}") from e
    except OSError as e:
        raise ConfigError(f"{path.name} 읽기 실패: {e}") from e
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as e:
        raise ConfigError(f"{path.name} 구문 오류: {e}") from e
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise ConfigError(f"{path.name} 의 최상위는 매핑이어야 합니다")
    return data
