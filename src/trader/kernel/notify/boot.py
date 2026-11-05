"""부트 결과(`boot`)로 알림기를 조립한다(기능 CLI·도구·분석 공용)."""

from __future__ import annotations

import logging
from typing import Any

from trader.kernel.notify import telegram
from trader.kernel.notify.telegram import Notifier, NullNotifier

logger = logging.getLogger("trader.notify")


def boot_notifier(boot: Any) -> Notifier:
    """settings `notify.enabled` 를 따르고(settings 가 없으면 켬), 시크릿이 없거나 조립이 실패하면
    (OSError·ValueError) 경고 후 NullNotifier — 알림 때문에 실행을 막지 않는다."""
    if getattr(boot, "secrets", None) is None:
        return NullNotifier()
    enabled = boot.settings.notify.enabled if boot.settings is not None else True
    try:
        return telegram.make_notifier(boot.secrets, enabled=enabled)
    except (OSError, ValueError) as e:
        logger.warning("알림 조립 실패, 알림 없이 진행: %s", type(e).__name__)
        return NullNotifier()
