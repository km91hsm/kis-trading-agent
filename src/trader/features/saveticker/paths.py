"""saveticker 수집 폴더·계정 파일 위치."""

from __future__ import annotations

from pathlib import Path

from trader.features.saveticker.settings import SaveTickerCfg, load_cfg
from trader.kernel.platform.paths import project_root, state_dir

DEFAULT_DIR = "state/external/saveticker"


def resolve_dir(configured: str | None) -> Path | None:
    """설정 경로(상대면 프로젝트 루트 기준). None/빈 문자열이면 None."""
    if not configured:
        return None
    p = Path(configured).expanduser()
    if not p.is_absolute():
        p = project_root() / p
    return p


def home(cfg: SaveTickerCfg | None = None) -> Path:
    """수집 폴더(`data/`·`runtime/` 의 부모). 수집기와 리더가 같은 폴더를 본다."""
    cfg = cfg or load_cfg()
    return resolve_dir(cfg.dir) or state_dir() / "external" / "saveticker"


def credentials_path() -> Path:
    """계정 파일(`SAVETICKER_EMAIL`·`SAVETICKER_PASSWORD`). 값은 수집 프로세스만 읽는다."""
    return project_root() / "secrets" / "saveticker.env"
