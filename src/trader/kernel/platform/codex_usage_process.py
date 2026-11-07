"""구독 조회 전용 프로세스 수명. 읽기 RPC가 끝나면 래퍼와 하위 프로세스를 함께 종료한다.

주문 세션의 종료 유예와 분리한다. stdout은 정확한 바이트 상한과 close의 잠금 대기 방지를
위해 비버퍼 binary pipe로 열고, UTF-8 JSON 디코딩은 호출자가 수행한다.
"""

from __future__ import annotations

import os
import signal
import subprocess
from contextlib import contextmanager

_TERM_GRACE_S = 0.25
_KILL_WAIT_S = 1.0


class _WindowsJob:
    """공개 Win32 API만 사용한다. 실행 전에 job에 넣어 부모 선종료 경합을 없앤다."""

    def __init__(self):
        import ctypes
        from ctypes import wintypes as w

        self.ctypes = ctypes
        self.api = ctypes.WinDLL("kernel32", use_last_error=True)
        self.handle = None
        for name, args, result in (
            ("CreateJobObjectW", [w.LPVOID, w.LPCWSTR], w.HANDLE),
            ("SetInformationJobObject", [w.HANDLE, ctypes.c_int, w.LPVOID, w.DWORD], w.BOOL),
            ("AssignProcessToJobObject", [w.HANDLE, w.HANDLE], w.BOOL),
            ("TerminateJobObject", [w.HANDLE, w.UINT], w.BOOL),
            ("OpenProcess", [w.DWORD, w.BOOL, w.DWORD], w.HANDLE),
            ("CloseHandle", [w.HANDLE], w.BOOL),
            ("CreateToolhelp32Snapshot", [w.DWORD, w.DWORD], w.HANDLE),
            ("Thread32First", [w.HANDLE, w.LPVOID], w.BOOL),
            ("Thread32Next", [w.HANDLE, w.LPVOID], w.BOOL),
            ("OpenThread", [w.DWORD, w.BOOL, w.DWORD], w.HANDLE),
            ("ResumeThread", [w.HANDLE], w.DWORD),
        ):
            function = getattr(self.api, name)
            function.argtypes, function.restype = args, result

        class BasicLimits(ctypes.Structure):
            _fields_ = [
                ("process_time", ctypes.c_int64),
                ("job_time", ctypes.c_int64),
                ("flags", w.DWORD),
                ("min_working_set", ctypes.c_size_t),
                ("max_working_set", ctypes.c_size_t),
                ("active_process_limit", w.DWORD),
                ("affinity", ctypes.c_size_t),
                ("priority", w.DWORD),
                ("scheduling", w.DWORD),
            ]

        class ExtendedLimits(ctypes.Structure):
            _fields_ = [
                ("basic", BasicLimits),
                ("io_counters", ctypes.c_uint64 * 6),
                ("process_memory", ctypes.c_size_t),
                ("job_memory", ctypes.c_size_t),
                ("peak_process_memory", ctypes.c_size_t),
                ("peak_job_memory", ctypes.c_size_t),
            ]

        self.handle = self.api.CreateJobObjectW(None, None)
        if not self.handle:
            raise ctypes.WinError(ctypes.get_last_error())
        limits = ExtendedLimits()
        limits.basic.flags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not self.api.SetInformationJobObject(
            self.handle, 9, ctypes.byref(limits), ctypes.sizeof(limits)
        ):
            error = ctypes.WinError(ctypes.get_last_error())
            self.close()
            raise error

    def assign_and_resume(self, pid: int) -> None:
        from ctypes import wintypes as w

        c, api = self.ctypes, self.api
        # PROCESS_SET_QUOTA | PROCESS_TERMINATE. Popen의 비공개 handle에 의존하지 않는다.
        process = api.OpenProcess(0x0100 | 0x0001, False, pid)
        if not process:
            raise c.WinError(c.get_last_error())
        try:
            if not api.AssignProcessToJobObject(self.handle, process):
                raise c.WinError(c.get_last_error())
        finally:
            api.CloseHandle(process)

        class ThreadEntry(c.Structure):
            _fields_ = [
                ("size", w.DWORD),
                ("usage", w.DWORD),
                ("thread_id", w.DWORD),
                ("process_id", w.DWORD),
                ("base_priority", w.LONG),
                ("delta_priority", w.LONG),
                ("flags", w.DWORD),
            ]

        snapshot = api.CreateToolhelp32Snapshot(0x00000004, 0)  # TH32CS_SNAPTHREAD
        if snapshot == c.c_void_p(-1).value:
            raise c.WinError(c.get_last_error())
        try:
            entry = ThreadEntry()
            entry.size = c.sizeof(entry)
            found = api.Thread32First(snapshot, c.byref(entry))
            while found:
                if entry.process_id == pid:
                    thread = api.OpenThread(0x0002, False, entry.thread_id)  # SUSPEND_RESUME
                    if not thread:
                        raise c.WinError(c.get_last_error())
                    try:
                        if api.ResumeThread(thread) == 0xFFFFFFFF:
                            raise c.WinError(c.get_last_error())
                        return
                    finally:
                        api.CloseHandle(thread)
                entry.size = c.sizeof(entry)
                found = api.Thread32Next(snapshot, c.byref(entry))
            raise OSError("suspended process thread was not found")
        finally:
            api.CloseHandle(snapshot)

    def close(self):
        if self.handle:
            self.api.TerminateJobObject(self.handle, 1)
            self.api.CloseHandle(self.handle)
            self.handle = None


def _signal_tree(pid: int, sig: int) -> None:
    try:
        os.killpg(pid, sig)
    except ProcessLookupError:
        pass


@contextmanager
def spawn(argv: list[str], *, cwd: str, env: dict[str, str]):
    """새 그룹/Job Object와 비버퍼 pipe를 소유한다. 부모가 먼저 끝나도 후손을 정리한다."""
    job = _WindowsJob() if os.name == "nt" else None
    proc = None
    try:
        options = (
            # CREATE_SUSPENDED | CREATE_NEW_PROCESS_GROUP | CREATE_NO_WINDOW
            {"creationflags": 0x00000004 | 0x00000200 | 0x08000000}
            if job is not None
            else {"start_new_session": True}
        )
        proc = subprocess.Popen(
            argv,
            cwd=cwd,
            env=env,
            shell=False,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            bufsize=0,
            **options,
        )
        if job is not None:
            job.assign_and_resume(proc.pid)
        yield proc
    finally:
        if job is not None:
            job.close()
        if proc is not None:
            if job is None:
                _signal_tree(proc.pid, signal.SIGTERM)
                try:
                    proc.wait(timeout=_TERM_GRACE_S)
                except subprocess.TimeoutExpired:
                    pass
                # 리더의 종료는 후손의 종료를 뜻하지 않는다. 그룹에는 항상 KILL을 보낸다.
                _signal_tree(proc.pid, signal.SIGKILL)
            try:
                proc.wait(timeout=_KILL_WAIT_S)
            except subprocess.TimeoutExpired:
                proc.kill()
                try:
                    proc.wait(timeout=_KILL_WAIT_S)
                except subprocess.TimeoutExpired:
                    pass
            # FileIO에는 BufferedReader의 read lock이 없다. 기본 Popen.__exit__도 쓰지 않는다.
            for stream in (proc.stdin, proc.stdout):
                if stream is not None:
                    stream.close()
