"""universe.yaml(분석 유니버스)·sectors.yaml(종목 → 대분류) 모델과 로더."""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from trader.kernel.config.errors import ConfigError
from trader.kernel.config.sections import Section
from trader.kernel.config.yaml_io import read_yaml
from trader.kernel.platform import paths

Market = Literal["KR", "US"]


class UsSymbol(BaseModel):
    model_config = ConfigDict(extra="forbid")

    symbol: str
    exchange: Literal["NAS", "NYS", "AMS"]


class Universe(BaseModel):
    # frozen: load_universe 가 같은 객체를 여러 호출자에 돌려준다(파일 mtime 메모)
    model_config = ConfigDict(extra="forbid", frozen=True)

    kr: list[str] = Field(default_factory=list)
    us: list[UsSymbol] = Field(default_factory=list)
    exclude: list[str] = Field(default_factory=list)
    watch: list[str] = Field(default_factory=list)


class SectorMap(BaseModel):
    """config/sectors.yaml — 종목 → 동종업 대분류 (사람이 관리하는 결정적 데이터).

    피처 스토어 `*_vs_sector`(비율 − 같은 날·시장·섹터 중앙값)와 `00_inputs.fundamental.peer`
    가 쓴다."""

    # frozen: load_sectors 가 같은 객체를 여러 호출자에 돌려준다(파일 mtime 메모)
    model_config = ConfigDict(extra="forbid", frozen=True)

    sectors: list[str] = Field(default_factory=list)
    kr: dict[str, str] = Field(default_factory=dict)
    us: dict[str, str] = Field(default_factory=dict)
    # KIS 업종 → 대분류 (KR 은 현재가 응답의 업종한글명, US 는 현재가상세 e_icod — 한글 업종명).
    # `trader universe sectors` 가 빠진 종목의 섹터를 제안할 때 쓴다. 표에 없으면 추측하지 않음.
    kr_industry_to_sector: dict[str, str] = Field(default_factory=dict)
    us_industry_to_sector: dict[str, str] = Field(default_factory=dict)

    @field_validator("kr", "us", "kr_industry_to_sector", "us_industry_to_sector")
    @classmethod
    def _declared(cls, v: dict[str, str], info) -> dict[str, str]:
        declared = set(info.data.get("sectors") or [])
        bad = sorted({s for s in v.values() if s not in declared})
        if bad:
            raise ValueError(f"sectors 에 없는 대분류: {bad}")
        return v

    def sector_of(self, ticker: str, market: Market) -> str | None:
        table = self.kr if market == "KR" else self.us
        return table.get(ticker)

    def classify(self, industry: str | None, market: Market) -> str | None:
        if not industry:
            return None
        table = self.kr_industry_to_sector if market == "KR" else self.us_industry_to_sector
        return table.get(str(industry).strip())


# 커널 섹션 등록은 템플릿 로드·키 검증용이다. 교차 필드 검증은 load_* 가 전체 모델로 한다.
UNIVERSE_SECTIONS: tuple[Section, ...] = (
    Section("universe", "kr", list[str], list),
    Section("universe", "us", list[UsSymbol], list),
    Section("universe", "exclude", list[str], list),
    Section("universe", "watch", list[str], list),
)

SECTOR_SECTIONS: tuple[Section, ...] = (
    Section("sectors", "sectors", list[str], list),
    Section("sectors", "kr", dict[str, str], dict),
    Section("sectors", "us", dict[str, str], dict),
    Section("sectors", "kr_industry_to_sector", dict[str, str], dict),
    Section("sectors", "us_industry_to_sector", dict[str, str], dict),
)

_MEMO: dict[tuple[str, str], tuple[tuple, Any]] = {}
_MEMO_LOCK = threading.Lock()


def _file_sig(p: Path) -> tuple:
    """파일 변경 감지 값(mtime_ns·크기). 없으면 ('missing',)."""
    try:
        st = p.stat()
    except OSError:
        return ("missing",)
    return (st.st_mtime_ns, st.st_size)


def _memo_load(model: type[BaseModel], p: Path) -> Any:
    """yaml → 검증된 frozen 모델을 (경로, 모델) 별로 기억한다. 파일 mtime·크기가 바뀌면 다시
    읽고, 검증 오류는 기억하지 않는다(고치면 바로 반영)."""
    key = (str(p.resolve()), model.__name__)
    sig = _file_sig(p)
    with _MEMO_LOCK:
        hit = _MEMO.get(key)
        if hit is not None and hit[0] == sig:
            return hit[1]
    try:
        obj = model.model_validate(read_yaml(p))
    except ValidationError as e:
        parts = [f"{'.'.join(str(x) for x in err['loc']) or '(루트)'}: {err['msg']}"
                 for err in e.errors()]  # fmt: skip
        raise ConfigError(f"{p.name} 검증 실패: {'; '.join(parts)}") from e
    with _MEMO_LOCK:
        _MEMO[key] = (sig, obj)
    return obj


def load_universe(path: Path | None = None) -> Universe:
    """파일이 없으면 빈 유니버스."""
    return _memo_load(Universe, path or paths.config_dir() / "universe.yaml")


def load_sectors(path: Path | None = None) -> SectorMap:
    """파일이 없으면 빈 맵 (파생 열은 전부 NaN, peer 절은 건너뜀)."""
    return _memo_load(SectorMap, path or paths.config_dir() / "sectors.yaml")
