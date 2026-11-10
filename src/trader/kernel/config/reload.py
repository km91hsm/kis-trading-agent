"""실행 중 프로세스(데몬·웹)가 settings.yaml 변경을 재시작 없이 반영하게 하는 감시기.

`poll()` 은 파일 서명(mtime_ns·크기)이 바뀌었을 때만 다시 읽어 새 모델을 돌려준다. 검증에
실패하면 None(호출자는 이전 설정을 유지)이고, 같은 오류 문구는 한 번만 `on_error` 로 알린다.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from pathlib import Path

from pydantic import BaseModel

from trader.kernel.config.errors import ConfigError
from trader.kernel.config.loader import _file_sig, load
from trader.kernel.config.sections import SectionRegistry
from trader.kernel.platform import paths

logger = logging.getLogger("trader.config")


class SettingsWatcher:
    def __init__(
        self,
        registry: SectionRegistry,
        *,
        path: Path | None = None,
        on_error: Callable[[str], None] | None = None,
    ) -> None:
        self._registry = registry
        self._path = path or paths.config_dir() / "settings.yaml"
        self._on_error = on_error
        self._sig = _file_sig(self._path)
        self._last_error: str | None = None
        self._lock = threading.Lock()

    def poll(self) -> BaseModel | None:
        """바뀌었고 검증을 통과하면 새 설정, 아니면 None. 여러 스레드가 불러도 한 번만 읽는다."""
        sig = _file_sig(self._path)
        if sig == self._sig:
            return None
        with self._lock:
            if sig == self._sig:
                return None
            self._sig = sig
            try:
                obj = load("settings", self._registry, self._path)
            except ConfigError as e:
                self._report(str(e))
                return None
            self._last_error = None
        logger.info("settings.yaml 변경을 반영했습니다")
        return obj

    def _report(self, msg: str) -> None:
        if msg == self._last_error:
            return
        self._last_error = msg
        logger.warning("settings.yaml 을 다시 읽지 못해 이전 설정을 유지합니다: %s", msg)
        if self._on_error is None:
            return
        try:
            self._on_error(msg)
        except Exception:  # noqa: BLE001 - 알림 실패가 감시를 막지 않는다
            logger.debug("설정 오류 알림 실패", exc_info=True)
