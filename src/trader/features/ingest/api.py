"""ingest 공개면. 다른 기능은 이 모듈만 import 한다."""

from trader.features.ingest import disclosures, indicators, manual_inputs
from trader.features.ingest.base import (
    MAX_PAST,
    SKIP_REASONS,
    Section,
    owned_client,
    run_section,
    skipped_section,
    strip_tags,
)
from trader.features.ingest.clients import build_dart, build_edgar, build_input_deps
from trader.features.ingest.dart import (
    ANNUAL,
    RATIO_KEYS,
    DartClient,
    DartError,
    basic_ratios,
)
from trader.features.ingest.edgar import EdgarClient, ratios_by_year_us
from trader.features.ingest.input_quality import activity_context, macro_context, source_coverage
from trader.features.ingest.inputs import (
    SCHEMA_VERSION,
    InputDeps,
    build_inputs,
    collect_and_save,
    jsonable,
)
from trader.features.ingest.macro import Fred, collect_macro
from trader.features.ingest.naver_blog import NaverBlogSource
from trader.features.ingest.news import MACRO_QUERIES, GoogleNewsRss
from trader.features.ingest.news_store import (
    count,
    insert_items,
    normalize_title,
    novel_items,
    recent,
    row_to_dict,
    universe_names,
)
from trader.features.ingest.research_context import collect_research_context
from trader.features.ingest.settings import MacroCfg, NewsCfg, SourcesConfig, load_sources
from trader.features.ingest.universe import (
    SectorMap,
    Universe,
    UsSymbol,
    load_sectors,
    load_universe,
)

# 서브모듈은 `disclosures.critical_recent`, `manual_inputs.{read, read_market, append_market}`,
# `indicators.{COLUMNS, latest, indicator_frame, to_frame}` 로 쓴다.
__all__ = [
    "ANNUAL",
    "MACRO_QUERIES",
    "MAX_PAST",
    "RATIO_KEYS",
    "SCHEMA_VERSION",
    "SKIP_REASONS",
    "DartClient",
    "DartError",
    "EdgarClient",
    "Fred",
    "GoogleNewsRss",
    "InputDeps",
    "MacroCfg",
    "NaverBlogSource",
    "NewsCfg",
    "SectorMap",
    "Section",
    "SourcesConfig",
    "Universe",
    "UsSymbol",
    "activity_context",
    "macro_context",
    "source_coverage",
    "basic_ratios",
    "build_dart",
    "build_edgar",
    "build_input_deps",
    "build_inputs",
    "collect_and_save",
    "collect_macro",
    "collect_research_context",
    "count",
    "disclosures",
    "indicators",
    "insert_items",
    "jsonable",
    "load_sectors",
    "load_sources",
    "load_universe",
    "manual_inputs",
    "normalize_title",
    "novel_items",
    "owned_client",
    "ratios_by_year_us",
    "recent",
    "row_to_dict",
    "run_section",
    "skipped_section",
    "strip_tags",
    "universe_names",
]
