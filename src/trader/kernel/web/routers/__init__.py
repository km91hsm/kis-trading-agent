"""커널 웹 자원 라우터. `create_app` 이 `/api` 아래에 건다(기능 이름 없음)."""

from __future__ import annotations

from trader.kernel.web.routers import jobs, runs, system

KERNEL_ROUTERS = (system.router, system.settings_router, jobs.router, runs.router)
