"""Process elevation helpers.

Windows uses integrity levels that can block simulated input from a normal
FlowClone process into an elevated target. Linux does not expose the same
UIPI model, so the public helpers become harmless no-ops there.
"""
from __future__ import annotations

import ctypes
import os
import sys
from typing import Optional


if sys.platform != "win32":

    def integrity_level() -> Optional[int]:
        return None


    def is_elevated() -> bool:
        return False


    def relaunch_as_admin() -> bool:
        return False


else:
    from ctypes import wintypes

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

    def integrity_level() -> Optional[int]:
        """Return the current process integrity RID, or None on error."""
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
        """True if running at High integrity or above."""
        rid = integrity_level()
        return rid is not None and rid >= _INTEGRITY_HIGH


    def _shell_execute_runas(exe: str, params: str, workdir: str) -> bool:
        _shell32.ShellExecuteW.argtypes = [
            wintypes.HWND, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.LPCWSTR,
            wintypes.LPCWSTR, ctypes.c_int,
        ]
        _shell32.ShellExecuteW.restype = wintypes.HINSTANCE
        result = _shell32.ShellExecuteW(
            None, "runas", exe, params, workdir, 1,
        )
        return int(ctypes.cast(result, ctypes.c_void_p).value or 0) > 32


    def relaunch_as_admin() -> bool:
        """Spawn a new elevated instance of the app via UAC."""
        if is_elevated():
            return False

        exe = sys.executable
        if getattr(sys, "frozen", False):
            params = ""
        else:
            main_script = os.path.abspath(sys.argv[0]) if sys.argv else "main.py"
            params = f'"{main_script}"'
        workdir = (
            os.path.dirname(os.path.abspath(sys.argv[0]))
            if sys.argv
            else os.getcwd()
        )
        return _shell_execute_runas(exe, params, workdir)
