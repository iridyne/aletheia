"""Pure-ctypes Win32 process introspection — zero subprocess spawns.

Replaces wmic/tasklist/powershell calls for process liveness and command-line
reading. On Win11, spawning ANY console-subsystem .exe from a detached parent
can flash a Windows Terminal window. This module avoids that entirely by using
kernel32/ntdll directly via ctypes.

Only imported on Windows; POSIX callers never reach this module.
"""

import ctypes
import ctypes.wintypes as wt
from ctypes import byref, sizeof, POINTER, Structure, c_void_p, c_ulong

# --- Win32 constants ---

PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
PROCESS_VM_READ = 0x0010
SYNCHRONIZE = 0x00100000
TH32CS_SNAPPROCESS = 0x00000002
STILL_ACTIVE = 259
STATUS_SUCCESS = 0
# WaitForSingleObject return codes.
WAIT_OBJECT_0 = 0x00000000   # the process has terminated (signaled)
WAIT_TIMEOUT = 0x00000102    # still running after the (zero) wait
ProcessCommandLineInformation = 60
INVALID_HANDLE_VALUE = c_void_p(-1).value


# --- Structures ---

class PROCESSENTRY32W(Structure):
    _fields_ = [
        ("dwSize", wt.DWORD),
        ("cntUsage", wt.DWORD),
        ("th32ProcessID", wt.DWORD),
        ("th32DefaultHeapID", POINTER(c_ulong)),
        ("th32ModuleID", wt.DWORD),
        ("cntThreads", wt.DWORD),
        ("th32ParentProcessID", wt.DWORD),
        ("pcPriClassBase", ctypes.c_long),
        ("dwFlags", wt.DWORD),
        ("szExeFile", ctypes.c_wchar * 260),
    ]


class UNICODE_STRING(Structure):
    _fields_ = [
        ("Length", wt.USHORT),
        ("MaximumLength", wt.USHORT),
        ("Buffer", ctypes.c_wchar_p),
    ]


# --- DLL references ---

_kernel32 = ctypes.windll.kernel32
_ntdll = ctypes.windll.ntdll

_kernel32.OpenProcess.restype = wt.HANDLE
_kernel32.OpenProcess.argtypes = [wt.DWORD, wt.BOOL, wt.DWORD]

_kernel32.CloseHandle.restype = wt.BOOL
_kernel32.CloseHandle.argtypes = [wt.HANDLE]

_kernel32.CreateToolhelp32Snapshot.restype = wt.HANDLE
_kernel32.CreateToolhelp32Snapshot.argtypes = [wt.DWORD, wt.DWORD]

_kernel32.Process32FirstW.restype = wt.BOOL
_kernel32.Process32FirstW.argtypes = [wt.HANDLE, POINTER(PROCESSENTRY32W)]

_kernel32.Process32NextW.restype = wt.BOOL
_kernel32.Process32NextW.argtypes = [wt.HANDLE, POINTER(PROCESSENTRY32W)]

_kernel32.GetExitCodeProcess.restype = wt.BOOL
_kernel32.GetExitCodeProcess.argtypes = [wt.HANDLE, POINTER(wt.DWORD)]

_kernel32.WaitForSingleObject.restype = wt.DWORD
_kernel32.WaitForSingleObject.argtypes = [wt.HANDLE, wt.DWORD]

_ntdll.NtQueryInformationProcess.restype = ctypes.c_long  # NTSTATUS
_ntdll.NtQueryInformationProcess.argtypes = [
    wt.HANDLE,     # ProcessHandle
    c_ulong,       # ProcessInformationClass
    c_void_p,      # ProcessInformation (buffer)
    c_ulong,       # ProcessInformationLength
    POINTER(c_ulong),  # ReturnLength
]

PROCESS_TERMINATE = 0x0001

# Pre-filter: only inspect processes whose exe matches these names.
_PYTHON_EXES = {"python.exe", "pythonw.exe", "python3.exe"}

_kernel32.TerminateProcess.restype = wt.BOOL
_kernel32.TerminateProcess.argtypes = [wt.HANDLE, ctypes.c_uint]


def _get_process_cmdline(handle: int) -> str | None:
    """Read the command line of an already-opened process handle.

    Uses NtQueryInformationProcess with ProcessCommandLineInformation (60),
    available on Windows 10 1511+ / Windows 11. Returns None on any failure.
    """
    # First call to get required buffer size.
    ret_len = c_ulong(0)
    status = _ntdll.NtQueryInformationProcess(
        handle, ProcessCommandLineInformation, None, 0, byref(ret_len)
    )
    # STATUS_INFO_LENGTH_MISMATCH = 0xC0000004 (expected on size query)
    if ret_len.value == 0:
        return None

    buf_size = ret_len.value
    buf = ctypes.create_string_buffer(buf_size)
    status = _ntdll.NtQueryInformationProcess(
        handle, ProcessCommandLineInformation, buf, buf_size, byref(ret_len)
    )
    if status != STATUS_SUCCESS:
        return None

    # Buffer starts with a UNICODE_STRING structure.
    us = UNICODE_STRING.from_buffer_copy(buf)
    if not us.Buffer or us.Length == 0:
        return None

    # Read the wchar string from the buffer (after the UNICODE_STRING header).
    header_size = sizeof(UNICODE_STRING)
    char_count = us.Length // 2
    if header_size + us.Length > buf_size:
        return None

    cmdline = ctypes.wstring_at(
        ctypes.addressof(buf) + header_size, char_count
    )
    return cmdline


def pid_alive_win32(pid: int, markers: tuple[str, ...]) -> bool:
    """True if pid is alive AND its command line contains any of the markers.

    Pure ctypes — no subprocess spawned. Returns False on any access error
    (process doesn't exist, access denied, etc.).
    """
    if pid <= 0:
        return False

    handle = _kernel32.OpenProcess(
        PROCESS_QUERY_LIMITED_INFORMATION | SYNCHRONIZE, False, pid
    )
    if not handle:
        return False

    try:
        # Authoritative liveness: WaitForSingleObject with a 0 timeout. A running
        # process is non-signaled → WAIT_TIMEOUT; a terminated one is signaled →
        # WAIT_OBJECT_0. This avoids the GetExitCodeProcess(STILL_ACTIVE=259)
        # ambiguity: a process that genuinely exits WITH code 259 would otherwise
        # be reported alive forever, so is_running() would never let the watchdog
        # resurrect a dead gateway. (If SYNCHRONIZE was somehow not granted, Wait
        # returns WAIT_FAILED — neither constant — and we fall through to dead.)
        if _kernel32.WaitForSingleObject(handle, 0) != WAIT_TIMEOUT:
            return False

        # Read command line and check markers.
        cmdline = _get_process_cmdline(handle)
        if cmdline is None:
            return False
        return any(m in cmdline for m in markers)
    finally:
        _kernel32.CloseHandle(handle)


def list_processes_win32() -> list[tuple[int, str]]:
    """Return [(pid, command_line)] for Python processes.

    Uses CreateToolhelp32Snapshot for enumeration (no subprocess spawn).
    Only reads command lines for processes whose exe name is python/pythonw
    (cheap pre-filter avoids opening hundreds of unrelated processes).
    """
    snapshot = _kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    if snapshot == INVALID_HANDLE_VALUE:
        return []

    results: list[tuple[int, str]] = []
    entry = PROCESSENTRY32W()
    entry.dwSize = sizeof(PROCESSENTRY32W)

    try:
        if not _kernel32.Process32FirstW(snapshot, byref(entry)):
            return []

        while True:
            exe_name = entry.szExeFile.lower()
            if exe_name in _PYTHON_EXES:
                pid = entry.th32ProcessID
                handle = _kernel32.OpenProcess(
                    PROCESS_QUERY_LIMITED_INFORMATION, False, pid
                )
                if handle:
                    try:
                        cmdline = _get_process_cmdline(handle)
                        if cmdline:
                            results.append((pid, cmdline))
                    finally:
                        _kernel32.CloseHandle(handle)

            if not _kernel32.Process32NextW(snapshot, byref(entry)):
                break
    finally:
        _kernel32.CloseHandle(snapshot)

    return results


def _get_child_pids(parent_pid: int) -> list[int]:
    """Return direct child PIDs of a given parent using CreateToolhelp32Snapshot."""
    snapshot = _kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    if snapshot == INVALID_HANDLE_VALUE:
        return []

    children: list[int] = []
    entry = PROCESSENTRY32W()
    entry.dwSize = sizeof(PROCESSENTRY32W)

    try:
        if not _kernel32.Process32FirstW(snapshot, byref(entry)):
            return []
        while True:
            if entry.th32ParentProcessID == parent_pid:
                children.append(entry.th32ProcessID)
            if not _kernel32.Process32NextW(snapshot, byref(entry)):
                break
    finally:
        _kernel32.CloseHandle(snapshot)

    return children


def kill_process_tree_win32(pid: int) -> None:
    """Terminate a process and all its descendants. Pure ctypes, no taskkill.exe.

    Traverses the process tree depth-first (children before parent) to prevent
    a supervisor from respawning a child we just killed. Never raises.
    """
    if pid <= 0:
        return

    # Collect entire tree via BFS, then terminate leaf-first.
    to_visit = [pid]
    all_pids: list[int] = []
    visited: set[int] = set()

    while to_visit:
        current = to_visit.pop(0)
        if current in visited:
            continue
        visited.add(current)
        all_pids.append(current)
        children = _get_child_pids(current)
        to_visit.extend(children)

    # Terminate in reverse order (deepest children first, root last).
    for target_pid in reversed(all_pids):
        try:
            handle = _kernel32.OpenProcess(PROCESS_TERMINATE, False, target_pid)
            if handle:
                try:
                    _kernel32.TerminateProcess(handle, 1)
                finally:
                    _kernel32.CloseHandle(handle)
        except Exception:
            pass
