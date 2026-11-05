"""jobs.build: JobSpec → ScheduledJob (per_market 펼침·enabled·schedule_key·게이트 검증)."""

from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from trader.kernel.config import KST, ConfigError
from trader.kernel.daemon.jobs import ScheduledJob, build
from trader.kernel.registry.types import Feature, JobSpec


def spec(**kw) -> JobSpec:
    base = dict(
        id="scan_{market}", kind="scan", trigger="cron", per_market=True,
        schedule_key="{market}.scan",
        interval_s=None, enabled=None, gate="none", heavy=False, misfire="default",
        catch_up="none", start_soon_s=0, body=lambda ctx, job: {},
    )  # fmt: skip
    base.update(kw)
    return JobSpec(**base)


def feat(*specs: JobSpec) -> Feature:
    return Feature(id="f", tier="lane", jobs=tuple(specs))


SCHEDULE = SimpleNamespace(
    kr=SimpleNamespace(scan="07:30"),
    us={"scan": {"time": "22:40", "tz": "America/New_York"}},
    reflect="16:30",
)
CTX = SimpleNamespace(gates={})


def run_build(features, *, settings=None, markets=("KR", "US"), schedule=SCHEDULE, ctx=CTX):
    return build(
        features, schedule=schedule, settings=settings or SimpleNamespace(),
        markets=list(markets), ctx=ctx,
    )  # fmt: skip


def test_build_expands_per_market_and_respects_enabled():
    s_all = spec()
    s_off = spec(id="off", per_market=False, schedule_key="reflect", enabled=lambda st: st.on)
    s_on = spec(id="on", per_market=False, schedule_key="reflect", enabled=lambda st: st.on)
    jobs = run_build([feat(s_all, s_off)], settings=SimpleNamespace(on=False))
    # Ruling P6: {market} 는 소문자로 — id `scan_kr`, schedule_key `kr.scan`
    assert [j.id for j in jobs] == ["scan_kr", "scan_us"]
    assert [j.market for j in jobs] == ["KR", "US"]
    assert all(isinstance(j, ScheduledJob) and j.spec is s_all for j in jobs)
    jobs = run_build([feat(s_on)], settings=SimpleNamespace(on=True))
    assert [(j.id, j.market) for j in jobs] == [("on", None)]


def test_cron_trigger_kwargs_read_time_and_tz_from_schedule():
    kr, us = run_build([feat(spec())])
    assert kr.trigger_kwargs == {"hour": 7, "minute": 30, "timezone": KST}
    assert us.trigger_kwargs == {"hour": 22, "minute": 40, "timezone": ZoneInfo("America/New_York")}
    assert kr.cron == {"hour": 7, "minute": 30} and kr.interval_s is None and kr.kind == "scan"


def test_cron_mapping_days_overrides_spec_days():
    schedule = SimpleNamespace(
        fri={"time": "14:30", "tz": "Asia/Seoul", "days": "fri"},
        obj=SimpleNamespace(time="09:00", days="mon"),
        plain="10:00",
        lst=[{"time": "08:00", "days": "sat"}, "08:30"],
    )

    def one(key, **kw):
        return run_build(
            [feat(spec(id="x", per_market=False, schedule_key=key, **kw))], schedule=schedule
        )

    assert one("fri", days="mon-fri")[0].trigger_kwargs["day_of_week"] == "fri"
    assert one("obj", days="tue")[0].trigger_kwargs["day_of_week"] == "mon"
    assert one("plain", days="mon-fri")[0].trigger_kwargs["day_of_week"] == "mon-fri"
    a, b = one("lst", days="mon-fri")
    assert (a.trigger_kwargs["day_of_week"], b.trigger_kwargs["day_of_week"]) == ("sat", "mon-fri")


def test_interval_job_trigger_kwargs():
    s = spec(id="poll", trigger="interval", per_market=False, schedule_key=None, interval_s=30)
    (job,) = run_build([feat(s)])
    assert job.trigger_kwargs == {"seconds": 30} and job.interval_s == 30 and job.cron is None


def test_interval_job_reads_seconds_from_schedule_key():
    sched = SimpleNamespace(news_collect=SimpleNamespace(interval_s=600))
    s = spec(
        id="news", trigger="interval", per_market=False,
        schedule_key="news_collect.interval_s", interval_s=1800,
    )  # fmt: skip
    (job,) = run_build([feat(s)], schedule=sched)
    assert job.trigger_kwargs == {"seconds": 600}


def test_interval_job_falls_back_to_interval_s_when_key_missing():
    s = spec(
        id="news", trigger="interval", per_market=False,
        schedule_key="news_collect.interval_s", interval_s=1800,
    )  # fmt: skip
    (job,) = run_build([feat(s)], schedule=SimpleNamespace())
    assert job.trigger_kwargs == {"seconds": 1800}


def test_per_market_interval_job_formats_schedule_key_with_market():
    """Ruling 15: per_market interval 잡도 schedule_key 의 {market} 을 소문자 시장으로 채운다 —
    yaml 값이 interval_s 기본값을 이긴다(키가 없는 시장만 폴백)."""
    s = spec(
        id="{market}_watch", trigger="interval", per_market=True,
        schedule_key="{market}.watch.interval_s", interval_s=60,
    )  # fmt: skip
    sched = {"kr": {"watch": {"interval_s": 30}}, "us": {}}
    kr, us = run_build([feat(s)], schedule=sched)
    assert (kr.id, kr.trigger_kwargs) == ("kr_watch", {"seconds": 30})
    assert (us.id, us.trigger_kwargs) == ("us_watch", {"seconds": 60})


def test_cron_job_without_schedule_key_raises_config_error():
    with pytest.raises(ConfigError, match="schedule_key"):
        run_build([feat(spec(schedule_key=None))])


def test_schedule_key_missing_in_schedule_raises_config_error():
    with pytest.raises(ConfigError, match="nope"):
        run_build([feat(spec(per_market=False, schedule_key="nope"))])


def test_bad_time_format_raises_config_error():
    sched = SimpleNamespace(reflect="25:99")
    with pytest.raises(ConfigError, match="HH:MM"):
        run_build([feat(spec(per_market=False, schedule_key="reflect"))], schedule=sched)


def test_interval_job_without_interval_raises_config_error():
    with pytest.raises(ConfigError, match="interval_s"):
        run_build([feat(spec(trigger="interval", per_market=False, interval_s=None))])


def test_unknown_gate_raises_config_error_but_known_gate_passes():
    s = spec(per_market=False, schedule_key="reflect", gate="market")
    with pytest.raises(ConfigError, match="market"):
        run_build([feat(s)])
    ctx = SimpleNamespace(gates={"market": lambda job: None})
    assert len(run_build([feat(s)], ctx=ctx)) == 1


def test_out_of_scope_attributes_pass_through_untouched():
    s = spec(per_market=False, schedule_key="reflect", heavy=True, misfire="scan", catch_up="queue")
    (job,) = run_build([feat(s)])
    assert job.spec.heavy is True and job.spec.misfire == "scan" and job.spec.catch_up == "queue"


def test_per_market_id_without_market_placeholder_raises():
    with pytest.raises(ConfigError, match="scan"):
        run_build([feat(spec(id="scan"))])


def test_list_schedule_value_expands_to_numbered_jobs_with_days():
    """schedule_key 값이 "HH:MM" 리스트면 시각마다 잡 하나(`id`, `id_1`, …), spec.days 는 요일."""
    s = spec(id="fills_sync", per_market=False, schedule_key="fills.times", days="mon-sat")
    sched = SimpleNamespace(fills=SimpleNamespace(times=["16:10", "07:10"]))
    jobs = run_build([feat(s)], schedule=sched)
    assert [j.id for j in jobs] == ["fills_sync", "fills_sync_1"]
    assert jobs[0].trigger_kwargs == {
        "hour": 16, "minute": 10, "timezone": KST, "day_of_week": "mon-sat",
    }  # fmt: skip
    assert jobs[1].trigger_kwargs["hour"] == 7 and jobs[1].cron["day_of_week"] == "mon-sat"


def test_single_string_keeps_one_job_and_days_is_optional():
    s = spec(id="one", per_market=False, schedule_key="reflect")
    (j,) = run_build([feat(s)])
    assert j.id == "one" and "day_of_week" not in j.trigger_kwargs
    (d,) = run_build([feat(spec(id="d", per_market=False, schedule_key="reflect", days="sat"))])
    assert d.trigger_kwargs["day_of_week"] == "sat"


def test_bad_time_in_list_is_config_error():
    s = spec(id="x", per_market=False, schedule_key="fills.times")
    with pytest.raises(ConfigError):
        run_build([feat(s)], schedule=SimpleNamespace(fills=SimpleNamespace(times=["16:10", "7"])))


def test_interval_from_settings_when_no_schedule_key():
    s = spec(
        id="probe", per_market=False, trigger="interval", schedule_key=None, interval_s=1800,
        interval_from=lambda st: st.minutes * 60,
    )  # fmt: skip
    (job,) = run_build([feat(s)], settings=SimpleNamespace(minutes=7))
    assert job.trigger_kwargs == {"seconds": 420}
    # interval_from 이 없으면 interval_s 그대로
    (job,) = run_build([feat(spec(id="p2", per_market=False, trigger="interval",
                                  schedule_key=None, interval_s=1800))])  # fmt: skip
    assert job.trigger_kwargs == {"seconds": 1800}
