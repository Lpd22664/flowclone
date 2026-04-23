"""Manage Windows auto-start via HKCU\\...\\Run registry key.

Only supported when running as a frozen PyInstaller EXE. In dev mode the
feature is inert — `is_supported()` returns False and UI callers should
disable the toggle with a tooltip explaining why.

Uses HKEY_CURRENT_USER (per-user) so no admin rights are needed. Writes to:
    HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Run
    value name: FlowClone
    value data: "C:\\path\\to\\FlowClone.exe"
"""
from __future__ import annotations

import sys
import winreg

RUN_KEY_PATH = r"Software\Microsoft\Windows\CurrentVersion\Run"
VALUE_NAME = "FlowClone"


def is_supported() -> bool:
    """True only when running as a frozen EXE. Auto-starting a dev `python main.py`
    invocation is fragile (venv path, working dir, etc.), so we don't offer it."""
    return bool(getattr(sys, "frozen", False))


def _exe_path() -> str:
    # sys.executable is the EXE itself in frozen mode.
    return sys.executable


def is_enabled() -> bool:
    """True if an auto-start entry for FlowClone exists and points to the
    currently-running EXE. If it points somewhere else (e.g. an old install
    location), we treat it as disabled — Save will overwrite it to the
    current path."""
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY_PATH, 0, winreg.KEY_READ) as key:
            value, _ = winreg.QueryValueEx(key, VALUE_NAME)
    except OSError:
        return False

    if not is_supported():
        return bool(value)
    # Normalise quotes + case for comparison
    current = _exe_path().strip('"').lower()
    stored = str(value).strip('"').lower()
    return stored == current


def enable() -> None:
    """Register the current EXE to run at login. Raises OSError on failure."""
    if not is_supported():
        raise RuntimeError("Auto-start is only available in installed builds.")
    with winreg.OpenKey(
        winreg.HKEY_CURRENT_USER, RUN_KEY_PATH, 0, winreg.KEY_SET_VALUE
    ) as key:
        # Quote the path so spaces in the path are handled correctly.
        value = f'"{_exe_path()}"'
        winreg.SetValueEx(key, VALUE_NAME, 0, winreg.REG_SZ, value)


def disable() -> None:
    """Remove the auto-start entry. Silent no-op if not present."""
    try:
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER, RUN_KEY_PATH, 0, winreg.KEY_SET_VALUE
        ) as key:
            winreg.DeleteValue(key, VALUE_NAME)
    except FileNotFoundError:
        pass
    except OSError:
        raise
