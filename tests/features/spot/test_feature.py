from trader.kernel.cli.boot import boot
from trader.kernel.registry.contracts import all_migrations


def test_boot_ports_has_accounts_and_inputs(project):
    b = boot(ports=True)
    try:
        assert {"accounts", "inputs"} <= set(b.ports)
        assert b.ports["accounts"].accounts() == []
    finally:
        b.conn.close()


def test_spot_is_lane_with_v6():
    from trader.features.spot.feature import FEATURE

    assert FEATURE.tier == "lane" and [m.version for m in FEATURE.migrations] == [6, 9]
    assert [m.owner for m in FEATURE.migrations] == [
        "spot",
        "spot",
    ]  # accounts 와 같은 기능 이름 규약
    assert [k.retry_guard for k in FEATURE.session_kinds] == [True, False]  # analyze 만 차단
    # 마지막 spot 소유는 9, 뒤의 커널 모듈(web=15)이 전체 끝
    assert [m.version for m in FEATURE.migrations][-1] == 9
    assert [m.version for m in all_migrations([FEATURE])][-1] == 15


def test_spot_declares_scan_analyze_job_queue_kinds_and_schedule():
    from trader.features.spot.feature import FEATURE
    from trader.features.spot.scan_analyze import SCAN_ANALYZE
    from trader.features.spot.settings import ScanAnalyzeSchedule

    assert SCAN_ANALYZE in FEATURE.jobs
    assert {"scan_analyze", "analyze_ticker"} <= {q.kind for q in FEATURE.queue_kinds}
    assert ("schedule", "scan_analyze", ScanAnalyzeSchedule) in [
        (s.file, s.key, s.model) for s in FEATURE.sections
    ]


def test_schedule_template_scan_analyze_and_regime_order(monkeypatch):
    """템플릿의 scan_analyze·regime 값이 모델 기본과 같고, 국면이 5분 앞선다."""
    from pathlib import Path

    import yaml

    from trader.features.regime.settings import RegimeSchedule
    from trader.features.spot.settings import ScanAnalyzeSchedule
    from trader.kernel.platform.bundle import templates_dir

    monkeypatch.setenv("TRADER_HOME", str(Path(__file__).resolve().parents[3]))
    raw = yaml.safe_load(
        (templates_dir() / "config" / "schedule.yaml").read_text(encoding="utf-8")
    )
    assert ScanAnalyzeSchedule(**raw["scan_analyze"]) == ScanAnalyzeSchedule()
    assert RegimeSchedule(**raw["regime"]) == RegimeSchedule()
    assert (raw["regime"]["kr"], raw["scan_analyze"]["kr"]) == ("07:25", "07:30")
    assert raw["regime"]["us"]["time"] == "08:25" and raw["scan_analyze"]["us"]["time"] == "08:30"
