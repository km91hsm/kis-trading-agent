"""로그온 시 데몬 + 웹 UI + 트레이(`app --tray --no-browser`) 자동 시작 등록 (스펙 §10.1).
파이썬 템플릿 + subprocess, 셸 스크립트 없음.

- macOS: ~/Library/LaunchAgents/com.trader.daemon.plist + `launchctl load -w`
- Windows: %USERPROFILE%/trader-daemon.xml + `schtasks /Create /TN trader-daemon /XML … /F`

작업 폴더·TRADER_HOME 은 `paths.project_root()`(번들은 OS 사용자 데이터 폴더). Windows XML 에는
env 가 없지만 번들의 기본 데이터 루트가 같은 폴더라 그대로 맞는다. 등록 여부는 템플릿 파일 존재로
본다(`installed`, 트레이 '로그인 시 자동 시작' 체크).
"""

from __future__ import annotations

import logging
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Literal
from xml.sax.saxutils import escape

from trader.kernel.config import ConfigError
from trader.kernel.platform import bundle, paths

logger = logging.getLogger("trader.platform")

LABEL = "com.trader.daemon"
TASK_NAME = "trader-daemon"
WATCHDOG_LABEL = "com.trader.watchdog"  # 데몬 밖 감시(5분 간격 1회 실행)
WATCHDOG_TASK = "trader-watchdog"
WATCHDOG_INTERVAL_S = 300

_PLIST = """<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>{label}</string>
  <key>ProgramArguments</key>
  <array>
{program_args}
  </array>
  <key>WorkingDirectory</key><string>{root}</string>
  <key>EnvironmentVariables</key>
  <dict>
    <key>TRADER_HOME</key><string>{root}</string>
    <key>PYTHONIOENCODING</key><string>utf-8</string>
    <key>PATH</key><string>{path_env}</string>
  </dict>
  <key>RunAtLoad</key><true/>
  <!-- 비정상 종료(exit != 0)일 때만 다시 띄운다. 잠금이 이미 잡혀 있으면(다른 인스턴스가 실행 중)
       app·daemon 은 안내 뒤 exit 0 으로 끝나므로 launchd 가 재기동하지 않는다. 설정 오류 등
       기동 실패(exit 1)는 ThrottleInterval 60초 간격으로 다시 시도한다 -->
  <key>KeepAlive</key><dict><key>SuccessfulExit</key><false/></dict>
  <key>ThrottleInterval</key><integer>60</integer>
  <key>ExitTimeOut</key><integer>{exit_timeout}</integer>
  <key>StandardOutPath</key><string>{out}</string>
  <key>StandardErrorPath</key><string>{err}</string>
</dict>
</plist>
"""

_TASK_XML = """<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo><Description>trader daemon (KIS auto trading)</Description></RegistrationInfo>
  <Triggers><LogonTrigger><Enabled>true</Enabled></LogonTrigger></Triggers>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <ExecutionTimeLimit>PT0S</ExecutionTimeLimit>
    <RestartOnFailure><Interval>PT1M</Interval><Count>3</Count></RestartOnFailure>
  </Settings>
  <Actions Context="Author">
    <Exec>
      <Command>{command}</Command>
      <Arguments>{arguments}</Arguments>
      <WorkingDirectory>{root}</WorkingDirectory>
    </Exec>
  </Actions>
</Task>
"""


_PLIST_INTERVAL = """<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>{label}</string>
  <key>ProgramArguments</key>
  <array>
{program_args}
  </array>
  <key>WorkingDirectory</key><string>{root}</string>
  <key>EnvironmentVariables</key>
  <dict>
    <key>TRADER_HOME</key><string>{root}</string>
    <key>PYTHONIOENCODING</key><string>utf-8</string>
    <key>PATH</key><string>{path_env}</string>
  </dict>
  <key>RunAtLoad</key><true/>
  <key>StartInterval</key><integer>{interval}</integer>
  <key>StandardOutPath</key><string>{out}</string>
  <key>StandardErrorPath</key><string>{err}</string>
</dict>
</plist>
"""

_TASK_XML_INTERVAL = """<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo><Description>trader watchdog</Description></RegistrationInfo>
  <Triggers>
    <TimeTrigger>
      <Repetition><Interval>PT{minutes}M</Interval><StopAtDurationEnd>false</StopAtDurationEnd></Repetition>
      <StartBoundary>2026-01-01T00:00:00</StartBoundary>
      <Enabled>true</Enabled>
    </TimeTrigger>
  </Triggers>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <ExecutionTimeLimit>PT10M</ExecutionTimeLimit>
  </Settings>
  <Actions Context="Author">
    <Exec>
      <Command>{command}</Command>
      <Arguments>{arguments}</Arguments>
      <WorkingDirectory>{root}</WorkingDirectory>
    </Exec>
  </Actions>
</Task>
"""


DEFAULT_PATH = ("/usr/local/bin", "/usr/bin", "/bin")
DEFAULT_EXIT_TIMEOUT_S = 660  # 세션 타임아웃 + 60초; cli 는 llm.timeout_s + 60 을 넘긴다


def render(
    platform: str,
    *,
    program: list[str],
    project_root: Path,
    label: str = LABEL,
    extra_paths: list[Path] | None = None,
    exit_timeout_s: int = DEFAULT_EXIT_TIMEOUT_S,
    interval_s: int | None = None,
) -> str:
    """program 은 등록 argv (`[python, "-m", "trader", "app", "--tray", "--no-browser"]` 또는 번들
    `[exe, "app", "--tray", "--no-browser"]`, 기본은 `bundle.service_argv()`). extra_paths(claude
    디렉터리 등)를 launchd PATH 앞에 둔다 (launchd 는 셸 PATH 가 없다).

    exit_timeout_s: SIGTERM 뒤 launchd 가 SIGKILL 하기까지의 시간. 데몬은 실행 중인 analyze
    세션(llm.timeout_s)을 기다린 뒤 잠금을 풀므로 그보다 길어야 한다 (기본 20초면 세션이 고아).
    interval_s 가 있으면 상시 실행이 아니라 그 간격으로 1회 실행하는 등록(watchdog)."""
    logs = project_root / "state" / "logs"
    name = "watchdog" if interval_s else "daemon"
    if platform == "darwin":
        dirs: list[str] = []
        for d in [*(extra_paths or []), Path(program[0]).parent, *map(Path, DEFAULT_PATH)]:
            # plist 는 macOS 전용이라 어느 호스트에서 렌더해도 POSIX 구분자여야 한다
            # (Windows CI 포함)
            posix = d.as_posix()
            if posix not in dirs:
                dirs.append(posix)
        common = dict(
            label=escape(label),
            program_args="\n".join(f"    <string>{escape(a)}</string>" for a in program),
            root=escape(str(project_root)),
            out=escape(str(logs / f"{name}.out")),
            err=escape(str(logs / f"{name}.err")),
            path_env=escape(":".join(dirs)),
        )
        if interval_s:
            return _PLIST_INTERVAL.format(interval=int(interval_s), **common)
        return _PLIST.format(exit_timeout=int(exit_timeout_s), **common)
    if platform == "win32":
        tpl = _TASK_XML_INTERVAL if interval_s else _TASK_XML
        return tpl.format(
            command=escape(program[0]),
            arguments=escape(" ".join(program[1:])),
            root=escape(str(project_root)),
            minutes=max(1, int((interval_s or 60) // 60)),
        )
    raise ConfigError(f"지원하지 않는 플랫폼입니다: {platform} (macOS·Windows 만)")


def _target(platform: str, home: Path, kind: str = "daemon") -> Path:
    label, task = (WATCHDOG_LABEL, WATCHDOG_TASK) if kind == "watchdog" else (LABEL, TASK_NAME)
    if platform == "darwin":
        return home / "Library" / "LaunchAgents" / f"{label}.plist"
    if platform == "win32":
        return home / f"{task}.xml"
    raise ConfigError(f"지원하지 않는 플랫폼입니다: {platform} (macOS·Windows 만)")


def _run(run: Callable[..., object], cmd: list[str]) -> None:
    r = run(cmd, shell=False, capture_output=True, encoding="utf-8", errors="replace")
    code = getattr(r, "returncode", 0)
    if code:
        err = (getattr(r, "stderr", "") or getattr(r, "stdout", "") or "").strip()
        raise ConfigError(f"등록 명령 실패({code}): {' '.join(cmd)}: {err}")


def install(
    *,
    dry_run: bool,
    run: Callable[..., object] = subprocess.run,
    platform: str = sys.platform,
    home: Path | None = None,
    program: list[str] | None = None,
    project_root: Path | None = None,
    extra_paths: list[Path] | None = None,
    exit_timeout_s: int = DEFAULT_EXIT_TIMEOUT_S,
    kind: Literal["daemon", "watchdog"] = "daemon",
    reload: bool = True,
) -> dict:
    """템플릿을 쓰고 OS 등록 명령을 실행한다. dry_run 이면 아무것도 쓰지 않고 명령만 돌려준다.
    kind="watchdog" 은 `trader watchdog` 를 5분 간격 1회 실행으로 등록한다.

    reload=False(macOS 만): plist 만 쓰고 `launchctl unload/load` 는 하지 않는다 — launchd 가 띄운
    그 프로세스(트레이)가 다시 켤 때 unload 로 자기 자신을 내리지 않게. 다음 로그인부터 적용."""
    home = home or Path.home()
    watchdog = kind == "watchdog"
    program = program or (bundle.watchdog_argv() if watchdog else bundle.service_argv())
    project_root = project_root or paths.project_root()
    path = _target(platform, home, kind)
    text = render(
        platform,
        program=program,
        project_root=project_root,
        label=WATCHDOG_LABEL if watchdog else LABEL,
        extra_paths=extra_paths,
        exit_timeout_s=exit_timeout_s,
        interval_s=WATCHDOG_INTERVAL_S if watchdog else None,
    )
    task = WATCHDOG_TASK if watchdog else TASK_NAME
    if platform == "darwin":
        command = ["launchctl", "load", "-w", str(path)]
    else:
        command = ["schtasks", "/Create", "/TN", task, "/XML", str(path), "/F"]
    if dry_run:
        return {"path": path, "command": command, "written": False, "content": text}
    path.parent.mkdir(parents=True, exist_ok=True)
    (project_root / "state" / "logs").mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8" if platform == "darwin" else "utf-16")
    if platform == "darwin" and not reload:
        return {"path": path, "command": [], "written": True, "content": text}
    if platform == "darwin":  # 이미 로드된 라벨은 load 가 실패하므로 먼저 내린다 (실패 무시)
        run(
            ["launchctl", "unload", "-w", str(path)],
            shell=False,
            capture_output=True,
            encoding="utf-8",
            errors="replace",
        )
    _run(run, command)
    return {"path": path, "command": command, "written": True, "content": text}


def installed(
    *,
    platform: str = sys.platform,
    home: Path | None = None,
    kind: Literal["daemon", "watchdog"] = "daemon",
) -> bool:
    """로그온 등록 템플릿(plist·XML)이 있으면 True. 지원하지 않는 OS 는 False."""
    try:
        return _target(platform, home or Path.home(), kind).is_file()
    except ConfigError:
        return False


def uninstall(
    *,
    run: Callable[..., object] = subprocess.run,
    platform: str = sys.platform,
    home: Path | None = None,
    kind: Literal["daemon", "watchdog"] = "daemon",
    unload: bool = True,
) -> dict:
    """등록을 내리고 템플릿 파일을 지운다(Windows 는 파일이 없어도 작업 삭제를 시도). 멱등:
    Windows 에서 파일이 없고 작업 삭제가 실패하면 등록돼 있지 않은 것으로 보고 `note` 만
    남긴다(T9). 파일이 있는데 삭제가 실패하면 ConfigError.

    unload=False(macOS 만): `launchctl unload` 없이 plist 만 지운다 — launchd 가 띄운 그 프로세스
    안에서(트레이) 해제하면 unload 가 자기 자신을 SIGTERM 으로 내리기 때문이다. 다음 로그인부터
    시작되지 않는다."""
    home = home or Path.home()
    path = _target(platform, home, kind)
    task = WATCHDOG_TASK if kind == "watchdog" else TASK_NAME
    if platform == "darwin":
        command = ["launchctl", "unload", "-w", str(path)]
    else:
        command = ["schtasks", "/Delete", "/TN", task, "/F"]
    out: dict = {"path": path, "command": command}
    if path.exists():
        if platform == "darwin" and not unload:
            out["command"] = []
        else:
            _run(run, command)
        path.unlink()
    elif platform == "win32":
        try:
            _run(run, command)
        except ConfigError as e:
            logger.info("작업 삭제 실패 — 등록돼 있지 않은 것으로 봄: %s", e)
            out["note"] = "등록돼 있지 않음"
    return out


def launched_by_service(platform: str = sys.platform, env: dict | None = None) -> bool:
    """지금 프로세스가 로그온 등록(launchd 의 데몬 라벨)으로 떴는지. launchd 는 잡 프로세스에
    `XPC_SERVICE_NAME=<라벨>` 을 넣는다. Windows 작업 스케줄러는 삭제해도 실행 중인 작업을 죽이지
    않으므로 구분하지 않는다(False)."""
    import os

    env = os.environ if env is None else env
    return platform == "darwin" and env.get("XPC_SERVICE_NAME") == LABEL
