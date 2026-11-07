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

    def __init__(
        self,
        path: Path,
        *,
        now: Callable[[], datetime] | None = None,
        meta: dict[str, str] | None = None,
    ) -> None:
        self.path = path
        self._now = now or (lambda: datetime.now(UTC))  # 신선도·started_at 시계(데몬은 deps.now)
        self._meta = dict(meta or {})  # pid 파일에 함께 적을 표시용 값(전역 잠금의 데이터 루트)
        self._hold = None

    def _holder(self) -> tuple[int, str | None]:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            return int(data.get("pid", 0)), data.get("proc_start") or None
        except (ValueError, OSError, AttributeError):
            return 0, None

    def holder_meta(self) -> dict:
        """pid 파일 내용(표시용). 못 읽으면 빈 dict."""
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except (ValueError, OSError):
            return {}

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
                **self._meta,
                "pid": os.getpid(),
                "started_at": self._now().isoformat(),
                # pid 재사용 대비: 프로세스 시작 시각 식별자를 함께 적고 비교한다
                "proc_start": process_start(os.getpid()),
            },
            ensure_ascii=False,
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


# --- 사용자 단위 전역 데몬 잠금 ---
# 개발 폴더와 설치 앱은 데이터 루트가 달라 `state/daemon.lock` 만으로는 데몬 둘이 동시에 돌 수
# 있다(같은 계좌로 중복 주문). 데이터 루트와 무관한 고정 위치의 잠금을 하나 더 잡는다.
GLOBAL_LOCK_ENV = "TRADER_GLOBAL_LOCK_DIR"
GLOBAL_LOCK_NAME = "daemon-global.lock"


def global_lock_path() -> Path:
    """전역 데몬 잠금 경로: `TRADER_GLOBAL_LOCK_DIR`(테스트·특수 환경) 또는 `~/.trader/`."""
    env = os.environ.get(GLOBAL_LOCK_ENV)
    base = Path(env).expanduser() if env else Path.home() / ".trader"
    return base / GLOBAL_LOCK_NAME


def global_lock(root: Path, *, now: Callable[[], datetime] | None = None) -> Lock:
    """데이터 루트 `root` 소유로 기록하는 전역 잠금(잡지는 않는다)."""
    return Lock(global_lock_path(), now=now, meta={"root": str(_norm(root))})


def global_owner() -> str | None:
    """전역 잠금을 살아 있는 프로세스가 쥐고 있으면 그 소유 데이터 루트(모르면 빈 문자열), 아니면
    None."""
    lock = Lock(global_lock_path())
    if not lock.held():
        return None
    return str(lock.holder_meta().get("root") or "")


def other_root_running(root: Path) -> str | None:
    """다른 데이터 루트의 데몬이 전역 잠금을 쥐고 있으면 그 경로, 아니면 None(같은 루트는 기존
    `state/daemon.lock` 경로가 판단한다)."""
    owner = global_owner()
    if owner is None or owner == str(_norm(root)):
        return None
    return owner or "알 수 없음"


def other_root_message(owner: str) -> str:
    return f"다른 폴더({owner})의 데몬이 실행 중입니다 — 그 데몬을 먼저 종료하세요"


def _norm(root: Path) -> Path:
    try:
        return Path(root).resolve()
    except OSError:
        return Path(root)
