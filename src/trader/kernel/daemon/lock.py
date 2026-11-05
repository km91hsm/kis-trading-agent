"""데몬 단일 인스턴스 잠금: pid 파일(원자 생성·stale 판정) + 수명 동안 쥐는 OS 파일 잠금."""

from __future__ import annotations

import json
import logging
import os
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from trader.kernel.platform import filelock
from trader.kernel.platform.procs import process_start, same_process

logger = logging.getLogger("trader.daemon")


class Lock:
    """`<path>.os` 에 **수명 동안 쥐는 OS 파일 잠금**을 먼저 잡고(죽은 잠금 인수를 두 프로세스가
    동시에 판단해 둘 다 뜨는 경합을 OS 가 직렬화), 표시·생존 판정용 pid 파일(`path`)을 쓴다.
    pid 파일은 `heartbeat.read_status` 도 읽는다."""

    FRESH_S = 10  # 내용 없는 잠금이 이 시간 안에 생겼으면 다른 데몬이 쓰는 중

    def __init__(self, path: Path, *, now: Callable[[], datetime] | None = None) -> None:
        self.path = path
        self._now = now or (lambda: datetime.now(UTC))  # 신선도·started_at 시계(데몬은 deps.now)
        self._hold = None

    def _holder(self) -> tuple[int, str | None]:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            return int(data.get("pid", 0)), data.get("proc_start") or None
        except (ValueError, OSError, AttributeError):
            return 0, None

    def held(self) -> bool:
        """지금 살아 있는 (이 잠금을 잡은 그) 프로세스가 쥐고 있는지 — 잡지 않고 보기만 한다."""
        pid, start = self._holder()
        return bool(pid) and same_process(pid, start)

    def _fresh(self) -> bool:
        try:
            return (self._now().timestamp() - self.path.stat().st_mtime) < self.FRESH_S
        except OSError:
            return False

    def _create(self, tmp: Path, payload: str) -> None:
        """내용이 채워진 임시 파일을 잠금 이름에 원자적으로 붙인다(os.link 는 이미 있으면 실패).
        하드링크를 지원하지 않는 파일시스템이면 O_EXCL 생성 뒤 쓰기로 대신한다."""
        try:
            os.link(str(tmp), str(self.path))
        except FileExistsError:
            raise
        except OSError:
            fd = os.open(str(self.path), os.O_WRONLY | os.O_CREAT | os.O_EXCL)
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(payload)

    def acquire(self) -> bool:
        """OS 잠금 → pid 파일 순으로 잡는다. 이미 쥐고 있거나(같은 인스턴스 재호출 포함) 다른
        데몬이 살아 있으면 False. 죽은 데몬의 pid 파일만 넘겨받는다."""
        if self._hold is not None:
            logger.error("이 인스턴스가 이미 잠금을 쥐고 있습니다: %s", self.path)
            return False
        self.path.parent.mkdir(parents=True, exist_ok=True)
        hold = filelock.try_hold(self.path.with_name(self.path.name + ".os"))
        if hold is None:
            logger.error("데몬이 이미 실행 중입니다 (OS 잠금): %s", self.path)
            return False
        if not self._acquire_pidfile():
            filelock.release_hold(hold)
            return False
        self._hold = hold
        return True

    def _acquire_pidfile(self) -> bool:
        payload = json.dumps(
            {
                "pid": os.getpid(),
                "started_at": self._now().isoformat(),
                # pid 재사용 대비: 프로세스 시작 시각 식별자를 함께 적고 비교한다
                "proc_start": process_start(os.getpid()),
            }
        )
        tmp = self.path.with_name(f"{self.path.name}.{os.getpid()}.tmp")
        tmp.write_text(payload, encoding="utf-8")
        try:
            for _ in range(2):
                try:
                    self._create(tmp, payload)
                except FileExistsError:
                    pid, start = self._holder()
                    if pid and same_process(pid, start):
                        logger.error("데몬이 이미 실행 중입니다 (pid %s): %s", pid, self.path)
                        return False
                    if not pid and self._fresh():
                        logger.error("다른 데몬이 잠금을 만드는 중입니다: %s", self.path)
                        return False
                    logger.warning("죽은 데몬의 잠금을 넘겨받습니다 (pid %s)", pid)
                    self.path.unlink(missing_ok=True)
                    continue
                return True
            logger.error("잠금 경합이 반복돼 데몬을 띄우지 않습니다: %s", self.path)
            return False
        finally:
            tmp.unlink(missing_ok=True)

    def release(self) -> None:
        """내 pid 의 잠금 파일만 지우고 OS 잠금을 놓는다. 못 잡은 인스턴스는 건드리지 않는다."""
        if self._hold is None:
            return
        try:
            if self.path.exists():
                data = json.loads(self.path.read_text(encoding="utf-8"))
                if int(data.get("pid", 0)) == os.getpid():
                    self.path.unlink()
        except (ValueError, OSError, AttributeError):
            pass
        finally:
            filelock.release_hold(self._hold)
            self._hold = None
