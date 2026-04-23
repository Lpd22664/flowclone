"""Process elevation helpers (Windows).

Why this exists: Windows User Interface Privilege Isolation (UIPI) blocks
a lower-integrity process from delivering simulated keyboard input to a
higher-integrity foreground window. In practice: if FlowClone runs at
Medium integrity and the user's cmd.exe / PowerShell / Windows Terminal
is running elevated (High integrity), `SendInput` silently fails — it
returns 0 events inserted and the target window never sees the keys. That
presents to the user as 'push-to-talk works but nothing gets typed'.

The only real fix is to run FlowClone at the same or higher integrity
level. Rather than require users to always launch the app as admin from a
shortcut, we expose a 'Relaunch as administrator' option in the tray menu
and surface a clear overlay message when injection is blocked.

All functions are silent-safe — any failure returns False / None so the
caller can fall back without raising.
"""
from __future__ import annotations

import ctypes
import os
import sys
from ctypes import wintypes
from typing import Optional


_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
_advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
_shell32 = ctypes.WinDLL("shell32", use_last_error=True)

_kernel32.GetCurrentProcess.restype = wintypes.HANDLE
_kernel32.CloseHandle.argtypes = [wintypes.HANDLE]

_advapi32.OpenProcessToken.argtypes = [
    wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE),
]
_advapi32.OpenProcessToken.restype = wintypes.BOOL

_advapi32.GetTokenInformation.argtypes = [
    wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p,
    wintypes.DWORD, ctypes.POINTER(wintypes.DWORD),
]
_advapi32.GetTokenInformation.restype = wintypes.BOOL

_advapi32.GetSidSubAuthorityCount.argtypes = [ctypes.c_void_p]
_advapi32.GetSidSubAuthorityCount.restype = ctypes.POINTER(ctypes.c_ubyte)
_advapi32.GetSidSubAuthority.argtypes = [ctypes.c_void_p, wintypes.DWORD]
_advapi32.GetSidSubAuthority.restype = ctypes.POINTER(wintypes.DWORD)

_TOKEN_QUERY = 0x0008
_TokenIntegrityLevel = 25

_INTEGRITY_HIGH = 0x3000
_INTEGRITY_SYSTEM = 0x4000


def integrity_level() -> Optional[int]:
    """Return the integrity RID of the current process, or None on error.

    Common values:
        0x0000 Untrusted
        0x1000 Low
        0x2000 Medium
        0x3000 High (elevated)
        0x4000 System
    """
    try:
        tok = wintypes.HANDLE()
        if not _advapi32.OpenProcessToken(
            _kernel32.GetCurrentProcess(), _TOKEN_QUERY, ctypes.byref(tok),
        ):
            return None
        try:
            need = wintypes.DWORD()
            _advapi32.GetTokenInformation(
                tok, _TokenIntegrityLevel, None, 0, ctypes.byref(need),
            )
            if need.value == 0:
                return None
            buf = (ctypes.c_ubyte * need.value)()
            if not _advapi32.GetTokenInformation(
                tok, _TokenIntegrityLevel, buf, need.value, ctypes.byref(need),
            ):
                return None
            sid_ptr = ctypes.c_void_p.from_buffer(buf)
            n_sub = _advapi32.GetSidSubAuthorityCount(sid_ptr)[0]
            if n_sub == 0:
                return None
            return _advapi32.GetSidSubAuthority(sid_ptr, n_sub - 1)[0]
        finally:
            _kernel32.CloseHandle(tok)
    except Exception:
        return None


def is_elevated() -> bool:
    """True if running at High integrity or above (i.e. as administrator)."""
    rid = integrity_level()
    return rid is not None and rid >= _INTEGRITY_HIGH


def _shell_execute_runas(exe: str, params: str, workdir: str) -> bool:
    """ShellExecuteW with the 'runas' verb — triggers UAC.

    Return > 32 = success. Values <= 32 are error codes, most commonly:
        SE_ERR_ACCESSDENIED (5)  — user denied UAC
        0  — out of memory
    """
    _shell32.ShellExecuteW.argtypes = [
        wintypes.HWND, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.LPCWSTR,
        wintypes.LPCWSTR, ctypes.c_int,
    ]
    _shell32.ShellExecuteW.restype = wintypes.HINSTANCE
    SW_SHOWNORMAL = 1
    result = _shell32.ShellExecuteW(
        None, "runas", exe, params, workdir, SW_SHOWNORMAL,
    )
    # ShellExecuteW returns a value > 32 on success.
    return int(ctypes.cast(result, ctypes.c_void_p).value or 0) > 32


def relaunch_as_admin() -> bool:
    """Spawn a new elevated instance of the app. Returns True if the UAC
    prompt was shown (user may still decline). The caller is responsible
    for exiting the current non-elevated instance after a successful
    relaunch — otherwise the user has two FlowClones running."""
    if is_elevated():
        return False

    if getattr(sys, "frozen", False):
        # Frozen EXE: relaunch ourselves with no args.
        exe = sys.executable
        params = ""
    else:
        # Dev mode: relaunch the Python interpreter with the current script.
        exe = sys.executable
        # Original argv[0] should be main.py; quote it in case it has spaces.
        main_script = os.path.abspath(sys.argv[0]) if sys.argv else "main.py"
        params = f'"{main_script}"'
    workdir = os.path.dirname(os.path.abspath(sys.argv[0])) if sys.argv else os.getcwd()
    return _shell_execute_runas(exe, params, workdir)
