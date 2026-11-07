"""sources.yaml 의 수집원 7섹션(kis·news·macro·dart·edgar·community·channels)과 schedule 키."""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from trader.kernel.cli.boot import build_registry
from trader.kernel.config.loader import memo_load
from trader.kernel.config.sections import Section, SectionRegistry
from trader.kernel.registry import discover

DEFAULT_MARKET_QUERIES: dict[str, list[str]] = {
    "KR": [
        "증시",
        "코스피 OR 코스닥",
        "실적 발표",
        "수주 OR 계약",
        "신약 OR 임상",
        "반도체 OR 2차전지 OR 자동차",
        "금리 OR 환율",
    ],
    "US": [
        "stock market",
        "earnings",
        "Fed OR Treasury yields",
        "guidance OR upgrade",
        "semiconductor OR AI OR biotech",
    ],
}


class NewsCfg(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = True
    google_rss: bool = True
    naver: bool = True
    max_items: int = 20
    macro_items: int = 10
    # 시장 전체 뉴스 저장소: 데몬이 질의 묶음을 돌려 원장 news_items 에 쌓는다
    market_queries: dict[str, list[str]] = Field(
        default_factory=lambda: {k: list(v) for k, v in DEFAULT_MARKET_QUERIES.items()}
    )
    store_retention_days: int = Field(default=14, ge=1, le=365)
    store_hours: int = Field(default=48, ge=1, le=24 * 14)  # 세션·발굴 입력의 최근 창
    store_items: int = Field(default=60, ge=1, le=500)  # 발굴 입력 건수 상한


class MacroCfg(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = True
    kis_indices: bool = True
    fred: bool = True
    ecos: bool = True


class ToggleCfg(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = True


class EdgarCfg(BaseModel):
    """user_agent 가 비면 secrets 의 EDGAR_USER_AGENT 를 쓴다(공개 저장소에 연락처를 두지 않음)."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool = True
    user_agent: str = ""


class CommunityCfg(BaseModel):
    """커뮤니티 소스 기본값: robots·약관 허용만 on."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool = True
    reddit: bool = True
    clien: bool = True
    dcinside: bool = False
    fmkorea: bool = False
    toss: bool = False
    naver_board: bool = False
    max_posts: int = 50


class ChannelsCfg(BaseModel):
    model_config = ConfigDict(extra="forbid")

    youtube: list[str] = Field(default_factory=list)
    telegram: list[str] = Field(default_factory=list)
    naver_blog: list[str] = Field(default_factory=list)  # 블로그 id (rss.blog.naver.com/<id>.xml)
    naver_blog_posts: int = Field(default=5, ge=1, le=20)  # 키워드 무관 최근 글 수
    max_videos: int = Field(default=3, ge=1, le=20)
    max_posts: int = Field(default=30, ge=1, le=200)


class KisCfg(BaseModel):
    """daily_days 는 high_52w_pos(252봉)·sma_120 워밍업을 위해 260 이상이어야 한다."""

    model_config = ConfigDict(extra="forbid")

    daily_days: int = 260
    investor_days: int = 20


class NewsCollectSchedule(BaseModel):
    """schedule.yaml 의 news_collect: 뉴스 저장소 수집 주기."""

    model_config = ConfigDict(extra="forbid")

    interval_s: int = Field(default=1800, ge=1)


class SourcesConfig(BaseModel):
    """ingest 가 소유한 sources.yaml 7키 (다른 기능의 키는 담지 않는다)."""

    model_config = ConfigDict(extra="forbid")

    kis: KisCfg = Field(default_factory=KisCfg)
    news: NewsCfg = Field(default_factory=NewsCfg)
    macro: MacroCfg = Field(default_factory=MacroCfg)
    dart: ToggleCfg = Field(default_factory=ToggleCfg)
    edgar: EdgarCfg = Field(default_factory=EdgarCfg)
    community: CommunityCfg = Field(default_factory=CommunityCfg)
    channels: ChannelsCfg = Field(default_factory=ChannelsCfg)


SECTIONS: tuple[Section, ...] = (
    Section("sources", "kis", KisCfg, KisCfg),
    Section("sources", "news", NewsCfg, NewsCfg),
    Section("sources", "macro", MacroCfg, MacroCfg),
    Section("sources", "dart", ToggleCfg, ToggleCfg),
    Section("sources", "edgar", EdgarCfg, EdgarCfg),
    Section("sources", "community", CommunityCfg, CommunityCfg),
    Section("sources", "channels", ChannelsCfg, ChannelsCfg),
)

SCHEDULE_SECTION = Section("schedule", "news_collect", NewsCollectSchedule, NewsCollectSchedule)


def load_sources(
    path: Path | None = None, *, registry: SectionRegistry | None = None
) -> SourcesConfig:
    """sources.yaml 에서 ingest 의 7섹션만 뽑는다. 파일이 없으면 기본값, 잘못된 내용은
    ConfigError."""
    m = memo_load("sources", registry or build_registry(discover()), path)
    return SourcesConfig(**{k: getattr(m, k) for k in SourcesConfig.model_fields})
