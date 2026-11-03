"""`trader evaluation …` — 준비도·백테스트·모의 복기·보정 리포트 (옛 최상위 명령 이식)."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta

import typer

from trader.features.accounts import api as accounts_api
from trader.features.evaluation import backtest, calibration, paper_review, readiness
from trader.features.evaluation.memory import outcomes, store
from trader.features.evaluation.reflect import pending, run_reflect
from trader.features.ml import api as ml_api
from trader.kernel.cli.boot import boot, config_errors
from trader.kernel.config import KST, ConfigError
from trader.kernel.lane.types import LaneContext
from trader.kernel.platform import paths
from trader.kernel.session.runner import PipelineError

evaluation_app = typer.Typer(help="평가·복기·준비도·백테스트·RL 사이저", no_args_is_help=True)
calibration_app = typer.Typer(help="보정 리포트", no_args_is_help=True)
evaluation_app.add_typer(calibration_app, name="calibration")


@evaluation_app.command("readiness")
@config_errors
def readiness_cmd() -> None:
    """실전 전환 준비도 점검(읽기 전용). 모드는 바꾸지 않는다."""
    b = boot(ports=True)
    try:
        stock, _fo = accounts_api.split_accounts(accounts_api.load_accounts())
        r = readiness.check(
            b.conn,
            accounts=stock,  # 준비도는 현물 계좌만
            state=paths.state_dir(),
            today=datetime.now(KST).date(),
            interval_kinds=readiness.interval_kinds_of(b.features),
            calibration=ml_api.load_ml(b.registry).models.calibration,
        )
    finally:
        b.conn.close()
    typer.echo(json.dumps(r, ensure_ascii=False))


@evaluation_app.command("backtest")
@config_errors
def backtest_cmd(
    since: str = typer.Option(..., "--since", help="컷오프 날짜(YYYY-MM-DD). 이후 결정만 집계"),
    market: str | None = typer.Option(None, "--market", help="KR 또는 US (없으면 전체)"),
) -> None:
    """컷오프 이후 결정의 적중률·평균 수익·알파를 메모리 결과/피처 스토어 종가로 집계한다."""
    try:
        cutoff = date.fromisoformat(since)
        state = paths.state_dir()
    except (ValueError, paths.ProjectRootNotFound) as e:
        typer.echo(f"오류: {e}", err=True)
        raise typer.Exit(code=1) from e
    b = boot(conn=True)
    try:
        r = backtest.simple_backtest(b.conn, state, since=cutoff, market=market)
    finally:
        b.conn.close()
    typer.echo(json.dumps(r, ensure_ascii=False))


@evaluation_app.command("paper-review")
@config_errors
def paper_review_cmd(days: int = typer.Option(7, "--days", min=1, max=90)) -> None:
    """모의 계좌 결정 주문의 체결률·지정가 대비 슬리피지를 비용 가정과 비교한다(읽기 전용, JSON)."""
    b = boot(conn=True)
    try:
        today = datetime.now(KST).date()
        r = paper_review.compute(
            b.conn, accounts_api.load_accounts(), since=today - timedelta(days=days), until=today
        )
    finally:
        b.conn.close()
    typer.echo(json.dumps(r, ensure_ascii=False, indent=1))


@calibration_app.command("build")
@config_errors
def calibration_build() -> None:
    """현재 메모리 결과로 보정 리포트를 계산해 출력한다 (체크포인트는 analyze 때 종목별로 생성)."""
    b = boot(conn=True)
    try:
        cfg = ml_api.load_ml(b.registry).models.calibration
        r = calibration.build_calibration(
            b.conn,
            min_decisions=cfg.min_decisions,
            query_vector=None,
            asof=datetime.now(UTC).isoformat(),
        )
    finally:
        b.conn.close()
    typer.echo(
        json.dumps(r or {"active": False, "min_decisions": cfg.min_decisions}, ensure_ascii=False)
    )


@evaluation_app.command("reflect")
@config_errors
def reflect_cmd(
    market: str = typer.Option("KR", "--market", help="KR 또는 US"),
    dry_run: bool = typer.Option(False, "--dry-run", help="결과 채우기까지만 (세션 없음)"),
) -> None:
    """만기 결정의 결과를 채우고(피처 스토어 종가) reflector 세션으로 복기를 받는다 (스펙 §6.5)."""
    if market not in ("KR", "US"):
        raise ConfigError(f"시장은 KR 또는 US 여야 합니다: {market}")
    b = boot(ports=True)
    try:
        state = paths.state_dir()
        today = datetime.now(KST).date()
        matured = outcomes.mature(b.conn, state=state, today=today)
        typer.echo(f"결과 채움 {len(matured)}건")
        n_pending = len(pending(b.conn, market=market))
        if dry_run:
            typer.echo(f"대기 중 반성 {n_pending}건 (--dry-run: 세션 생략)")
            return
        ctx = LaneContext(
            b.settings,
            b.conn,
            state=state,
            now=lambda: datetime.now(UTC),
            dry_run=True,
            notifier=None,
            ports=b.ports,
            ext={"registry": b.registry},
        )
        res = run_reflect(ctx, market=market, date=today)
        if not res.ok:
            typer.echo(f"세션 실패: {res.error}", err=True)
            raise typer.Exit(code=1)
        pr = store.promote_and_prune(b.conn, now=datetime.now(UTC), settings=b.settings.memory)
        typer.echo(
            f"반성 {n_pending}건 ({'FAKE' if res.fake else 'session=' + str(res.session_id)}), "
            f"정리 {pr['deleted']}건 삭제"
        )
    except PipelineError as e:
        typer.echo(f"오류: {e}", err=True)
        raise typer.Exit(code=1) from e
    finally:
        b.conn.close()
