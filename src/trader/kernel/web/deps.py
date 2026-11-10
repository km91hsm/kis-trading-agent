"""웹 라우터가 `request.app.state.deps` 로 받는 의존성 묶음."""

from __future__ import annotations

import sqlite3
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from trader.kernel.registry.types import Feature


@dataclass(frozen=True)
class WebDeps:
    """웹 셸이 쓰는 의존성. `boot` 가 만들고 라우터는 읽기만 한다."""

    settings: Any
    conn_factory: Callable[[], sqlite3.Connection]
    state: Path
    token: str | None
    dist: Path
    now: Callable[[], datetime]
    # 루프백 밖에서 허용할 Host (settings.web.tailscale_hosts)
    extra_hosts: tuple[str, ...] = ()
    # 비루프백 Host 요청에 요구할 `Tailscale-User-Login` 허용 목록. 비면 Host 만 검사한다
    tailscale_users: tuple[str, ...] = ()
    # 외부(폰)에서 여는 주소(예: https://pc.tailnet.ts.net). 알림 링크 등에 쓴다
    public_base: str | None = None
    features: list[Feature] = ()  # type: ignore[assignment]
    # 기능이 내놓은 포트(이름 → 구현, `DaemonDeps.ports` 와 같은 모양).
    # 라우터는 커널 Protocol 로만 쓴다
    ports: Mapping[str, Any] = field(default_factory=dict)
    # settings.yaml 변경 감시(`config.reload.SettingsWatcher`, `poll() -> 새 설정|None`).
    # 있으면 요청마다 보고 바뀌었으면 `app.state.deps` 의 settings 를 바꾼다.
    # Host 허용 목록은 기동 때 값 그대로다
    settings_watch: Any = None
