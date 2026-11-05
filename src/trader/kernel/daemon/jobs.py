"""Feature 가 선언한 JobSpec → 스케줄러에 올릴 ScheduledJob. 커널은 잡 이름을 모른다."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any
from zoneinfo import ZoneInfo

from trader.kernel.config import KST, ConfigError
from trader.kernel.config.time import check_hm
from trader.kernel.registry.types import Feature, JobSpec


@dataclass(frozen=True)
class ScheduledJob:
    """펼쳐진 잡 한 개. trigger_kwargs: cron 은 {hour, minute, timezone}, interval 은 {seconds}."""

    id: str
    spec: JobSpec
    market: str | None
    trigger_kwargs: dict[str, Any]

    @property
    def kind(self) -> str:
        return self.spec.kind

    @property
    def cron(self) -> dict[str, int] | None:
        """하트비트·상태 표시용(JSON 직렬화 가능, 시간대 제외)."""
        if self.spec.trigger != "cron":
            return None
        return {k: v for k, v in self.trigger_kwargs.items() if k != "timezone"}

    @property
    def interval_s(self) -> int | None:
        return self.trigger_kwargs.get("seconds") if self.spec.trigger == "interval" else None


def _lookup(schedule: Any, path: str) -> Any:
    """점으로 이어진 경로를 매핑 키 또는 속성으로 따라간다."""
    cur = schedule
    for part in path.split("."):
        if isinstance(cur, Mapping):
            if part not in cur:
                raise ConfigError(f"schedule 에 '{path}' 항목이 없습니다")
            cur = cur[part]
        else:
            if not hasattr(cur, part):
                raise ConfigError(f"schedule 에 '{path}' 항목이 없습니다")
            cur = getattr(cur, part)
    return cur


def _cron_kwargs_list(schedule: Any, key: str, days: str | None) -> list[dict[str, Any]]:
    """schedule 항목이 "HH:MM" 리스트면 시각마다 kwargs 하나, 아니면 하나짜리 목록.
    값(매핑·객체)에 비어 있지 않은 `days` 가 있으면 JobSpec.days 보다 우선한다."""
    entry = _lookup(schedule, key)
    if isinstance(entry, (list, tuple)):
        if not entry:
            raise ConfigError(f"schedule '{key}' 은 비어 있을 수 없습니다")
        out = [_cron_kwargs(schedule, key, entry=e) for e in entry]
    else:
        out = [_cron_kwargs(schedule, key)]
    if days:
        for kw in out:
            kw.setdefault("day_of_week", days)
    return out


def _cron_kwargs(schedule: Any, key: str, *, entry: Any = None) -> dict[str, Any]:
    """schedule 항목은 "HH:MM" 이거나 `time`(+선택 `tz`·`days`)을 가진 매핑·객체다. tz 기본은 KST.
    entry 를 주면 조회 대신 그 값을 쓴다(리스트 원소)."""
    if entry is None:
        entry = _lookup(schedule, key)
    tz: Any = None
    day_of_week: Any = None
    if isinstance(entry, str):
        hm = entry
    else:
        hm = _lookup(entry, "time")
        try:
            tz = _lookup(entry, "tz")
        except ConfigError:
            tz = None
        try:
            day_of_week = _lookup(entry, "days")
        except ConfigError:
            day_of_week = None
    try:
        check_hm(hm)
    except ValueError as e:
        raise ConfigError(f"schedule '{key}' 은 HH:MM 형식이어야 합니다: {hm!r}") from e
    hour, minute = (int(x) for x in hm.split(":"))
    try:
        zone = ZoneInfo(tz) if tz else KST
    except (ValueError, OSError, KeyError) as e:
        raise ConfigError(f"schedule '{key}' 의 시간대가 올바르지 않습니다: {tz!r}") from e
    out: dict[str, Any] = {"hour": hour, "minute": minute, "timezone": zone}
    if day_of_week:
        out["day_of_week"] = day_of_week
    return out


def _interval_seconds(
    spec: JobSpec, schedule: Any, tag: str | None = None, settings: Any = None
) -> int:
    """interval 잡 주기(초). schedule_key 가 가리키는 값(정수 ≥ 1)을 우선하고, 키가 없거나 값이
    None 이면 spec.interval_s 로 폴백한다. 둘 다 없으면 ConfigError. per_market 잡은 schedule_key
    의 {market} 을 소문자 시장(tag)으로 채운다(id 와 같은 규칙). schedule_key 가 None 이고
    `interval_from` 이 있으면 `interval_from(settings)` 초를 쓴다."""
    if spec.schedule_key:
        key = spec.schedule_key.format(market=tag) if spec.per_market else spec.schedule_key
        try:
            value = _lookup(schedule, key)
        except ConfigError:
            value = None
        if value is not None:
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                raise ConfigError(
                    f"schedule '{key}' 은 1 이상의 정수(초)여야 합니다: {value!r}"
                )
            return value
    elif spec.interval_from is not None:
        return int(spec.interval_from(settings))
    if not spec.interval_s:
        raise ConfigError(f"잡 {spec.id}: interval 잡에는 interval_s 가 필요합니다")
    return int(spec.interval_s)


def build(
    features: list[Feature], *, schedule: Any, settings: Any, markets: list[str], ctx: Any
) -> list[ScheduledJob]:
    """기능이 선언한 JobSpec 을 ScheduledJob 으로 펼친다.
    per_market 이면 시장마다 `id.format(market=m.lower())`(schedule_key 도 소문자 — yaml `kr.…`),
    `enabled(settings)` 가 False 면 제외. 선언 오류(schedule_key·interval_s·미등록 게이트)는
    ConfigError. heavy·misfire·catch_up 등은 손대지 않고 spec 에 그대로 둔다."""
    gates = getattr(ctx, "gates", None) or {}
    out: list[ScheduledJob] = []
    for feature in features:
        for spec in feature.jobs:
            if spec.enabled is not None and not spec.enabled(settings):
                continue
            if spec.gate and spec.gate != "none" and spec.gate not in gates:
                raise ConfigError(f"잡 {spec.id}: 게이트 '{spec.gate}' 가 등록되지 않았습니다")
            if spec.per_market and "{market}" not in spec.id:
                raise ConfigError(f"잡 {spec.id}: per_market 잡의 id 에 {{market}} 가 필요합니다")
            for market in markets if spec.per_market else [None]:
                tag = market.lower() if market else None
                ident = spec.id.format(market=tag) if spec.per_market else spec.id
                if spec.trigger == "cron":
                    if not spec.schedule_key:
                        raise ConfigError(f"잡 {spec.id}: cron 잡에는 schedule_key 가 필요합니다")
                    key = (
                        spec.schedule_key.format(market=tag)
                        if spec.per_market
                        else spec.schedule_key
                    )
                    # 리스트 값은 시각마다 `id`, `id_1`, … 로 펼친다
                    for i, kwargs in enumerate(_cron_kwargs_list(schedule, key, spec.days)):
                        out.append(
                            ScheduledJob(ident if i == 0 else f"{ident}_{i}", spec, market, kwargs)
                        )
                    continue
                kwargs = {"seconds": _interval_seconds(spec, schedule, tag, settings)}
                out.append(ScheduledJob(ident, spec, market, kwargs))
    return out
