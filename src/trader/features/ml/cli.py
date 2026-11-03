"""`trader ml …` — 퀀트 모델 학습·튠·레지스트리(`train|tune|models|promote|demote`)와 피처 스토어
(`features snapshot|backfill|derive`). 옛 `trader ml …`·`trader features …` 이식."""

from __future__ import annotations

import json
from datetime import UTC, datetime

import typer

from trader.features.ingest import api as ingest_api
from trader.features.ml import features, registry
from trader.features.ml import train as train_mod
from trader.features.ml import tune as tune_mod
from trader.features.ml.settings import LabelCfg, load_ml
from trader.kernel.cli.boot import Boot, boot, config_errors
from trader.kernel.config import ConfigError
from trader.kernel.platform import paths

ml_app = typer.Typer(help="퀀트 모델 학습·레지스트리·피처 스토어", no_args_is_help=True)
features_app = typer.Typer(help="피처 스토어", no_args_is_help=True)
ml_app.add_typer(features_app, name="features")


def _market_client(b: Boot):
    """시세 클라이언트: accounts 포트의 `market_client()` (테스트 monkeypatch 지점)."""
    return b.ports["accounts"].market_client()


def _run_features(action: str, market: str | None, **kwargs) -> None:
    try:
        b = boot(ports=True)
        try:
            universe = ingest_api.load_universe()
            markets = [market] if market else ["KR", "US"]
            if any(m not in ("KR", "US") for m in markets):
                raise ConfigError(f"시장은 KR 또는 US 여야 합니다: {market}")
            market_client = _market_client(b)
            dart = ingest_api.build_dart(b.secrets)
            sources = ingest_api.load_sources(registry=b.registry)
            fn = features.backfill if action == "backfill" else features.snapshot
            n = fn(
                market_client=market_client,
                dart=dart,
                edgar=ingest_api.build_edgar(sources, b.secrets),
                universe=universe,
                markets=markets,  # type: ignore[arg-type]
                progress=lambda s: typer.echo(f"  {s}"),
                **kwargs,
            )
        finally:
            if b.conn is not None:
                b.conn.close()
    except (ConfigError, paths.ProjectRootNotFound) as e:
        typer.echo(f"오류: {e}", err=True)
        raise typer.Exit(code=1) from e
    except Exception as e:  # noqa: BLE001 - 시세 클라이언트 생성 실패 등
        typer.echo(f"오류: {type(e).__name__}: {e}", err=True)
        raise typer.Exit(code=1) from e
    typer.echo(f"{action}: {n}행 저장")


@features_app.command("snapshot")
@config_errors
def features_snapshot(
    market: str | None = typer.Option(None, "--market", help="KR 또는 US (기본: 둘 다)"),
    asof: str | None = typer.Option(
        None, "--asof", help="기준 시각 ISO8601 (24시간 넘은 과거는 거부)"
    ),
) -> None:
    """유니버스 전체의 오늘 피처 행을 저장한다."""
    try:
        when = datetime.fromisoformat(asof) if asof else None
    except ValueError as e:
        raise ConfigError(f"--asof 는 ISO8601 이어야 합니다: {asof!r}") from e
    if when is not None and when.tzinfo is None:
        when = when.replace(tzinfo=UTC)
    _run_features("snapshot", market, asof=when)


@features_app.command("backfill")
@config_errors
def features_backfill(
    years: int = typer.Option(3, "--years", help="과거 적재 연수 (최대 약 4년=1000봉)"),
    market: str | None = typer.Option(None, "--market", help="KR 또는 US (기본: 둘 다)"),
) -> None:
    """유니버스 전체의 과거 피처를 재구성해 저장한다."""
    _run_features("backfill", market, years=years)


@features_app.command("derive")
@config_errors
def features_derive() -> None:
    """모든 피처 파일의 파생 열(`*_vs_sector`, config/sectors.yaml)을 다시 계산한다.

    KIS 호출 없음 — 섹터 맵을 바꾸거나 옛 스토어를 올릴 때."""
    boot()
    n = features.rederive(progress=lambda s: typer.echo(f"  {s}"))
    typer.echo(f"derive: {n}개 파일 갱신")


@ml_app.command("train")
@config_errors
def ml_train(
    market: str | None = typer.Option(None, "--market", help="KR|US (기본 전체)"),
    min_rows: int = typer.Option(100, "--min-rows", help="학습 최소 라벨 행 수"),
    horizon: int | None = typer.Option(
        None,
        "--horizon",
        min=1,
        max=60,
        help="실험용: 이 실행만 label.horizon_days 를 덮어쓴다 (설정 파일 불변, 1~60)",
    ),
) -> None:
    """피처 스토어로 퀀트 모델을 워크포워드 학습·평가·저장하고 승격 기준을 판정한다."""
    b = boot()
    cfg = load_ml(b.registry)
    if horizon is not None:
        # model_copy(update=) 는 검증을 건너뛴다 → model_validate
        # (0·음수 호라이즌은 과거를 보는 라벨)
        label = LabelCfg.model_validate({**cfg.label.model_dump(), "horizon_days": horizon})
        cfg = cfg.model_copy(update={"label": label})
    if market is not None and market not in ("KR", "US"):
        raise ConfigError(f"시장은 KR 또는 US: {market}")
    r = train_mod.train_quant(cfg, market=market, min_rows=min_rows)
    typer.echo(json.dumps(r, ensure_ascii=False, default=str))


@ml_app.command("tune")
@config_errors
def ml_tune(
    market: str | None = typer.Option(None, "--market", help="KR|US (기본 전체)"),
    grid: str = typer.Option("small", "--grid", help="small|wide"),
    min_rows: int = typer.Option(100, "--min-rows", help="학습 최소 라벨 행 수"),
) -> None:
    """하이퍼파라미터 탐색: dev 폴드(마지막 폴드 제외)로 고르고 홀드아웃을 보고한다.

    자동 적용 없음 — 리포트를 보고 ml.yaml 에 반영한다."""
    b = boot()
    cfg = load_ml(b.registry)
    if market is not None and market not in ("KR", "US"):
        raise ConfigError(f"시장은 KR 또는 US: {market}")
    r = tune_mod.tune_quant(cfg, market=market, min_rows=min_rows, grid=grid)
    typer.echo(json.dumps(r, ensure_ascii=False, default=str))


@ml_app.command("models")
@config_errors
def ml_models() -> None:
    """레지스트리의 quant 모델 목록."""
    boot()
    metas = registry.list_models("quant")
    if not metas:
        typer.echo("모델 없음")
        return
    for m in metas:
        met = m.get("metrics") or {}
        typer.echo(
            f"{m['version']}  정확도 {met.get('accuracy', '-')}  "
            f"기준선 {met.get('baseline', '-')}  알파 {met.get('alpha', '-')}  "
            f"행 {m.get('rows', '-')}  {'승격' if m.get('promoted') else '미승격'}  "
            f"{m.get('promote_reason') or ''}"
        )


@ml_app.command("promote")
@config_errors
def ml_promote(
    version: str = typer.Argument(..., help="YYYYMMDD_HHMMSS"),
    reason: str = typer.Option("manual", "--reason"),
) -> None:
    """모델을 수동 승격한다 (이후 05_quant·scan 랭킹에 쓰인다)."""
    boot()
    if not any(m.get("version") == version for m in registry.list_models("quant")):
        typer.echo(f"오류: 모델 없음: {version}", err=True)
        raise typer.Exit(code=1)
    m = registry.promote("quant", version, reason=reason)
    typer.echo(f"승격: quant {m['version']} ({reason})")


@ml_app.command("demote")
@config_errors
def ml_demote(
    version: str = typer.Argument(..., help="YYYYMMDD_HHMMSS"),
    reason: str = typer.Option("manual", "--reason"),
) -> None:
    """승격을 해제한다 (다른 승격 버전이 없으면 05_quant·ML 랭킹 비활성)."""
    boot()
    if not any(m.get("version") == version for m in registry.list_models("quant")):
        typer.echo(f"오류: 모델 없음: {version}", err=True)
        raise typer.Exit(code=1)
    m = registry.demote("quant", version, reason=reason)
    typer.echo(f"승격 해제: quant {m['version']} ({reason})")
