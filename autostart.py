"""Manage per-user auto-start.

Only the Windows packaged build currently supports toggling auto-start from
the settings UI. Linux users can add a desktop autostart entry manually once
their venv path is stable.
"""
from __future__ import annotations

import sys


if sys.platform != "win32":

    def is_supported() -> bool:
        return False


    def is_enabled() -> bool:
        return False


    def enable() -> None:
        raise RuntimeError("Auto-start is not implemented for Linux yet.")


    def disable() -> None:
        return None


else:
    import winreg

    RUN_KEY_PATH = r"Software\Microsoft\Windows\CurrentVersion\Run"
    VALUE_NAME = "FlowClone"

    def is_supported() -> bool:
        """True only when running as a frozen EXE."""
        return bool(getattr(sys, "frozen", False))


    def _exe_path() -> str:
        return sys.executable


    def is_enabled() -> bool:
        try:
            with winreg.OpenKey(
                winreg.HKEY_CURRENT_USER, RUN_KEY_PATH, 0, winreg.KEY_READ,
            ) as key:
                value, _ = winreg.QueryValueEx(key, VALUE_NAME)
        except OSError:
            return False

        if not is_supported():
            return bool(value)
        current = _exe_path().strip('"').lower()
        stored = str(value).strip('"').lower()
        return stored == current


    def enable() -> None:
        if not is_supported():
            raise RuntimeError("Auto-start is only available in installed builds.")
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER, RUN_KEY_PATH, 0, winreg.KEY_SET_VALUE,
        ) as key:
            winreg.SetValueEx(key, VALUE_NAME, 0, winreg.REG_SZ, f'"{_exe_path()}"')


    def disable() -> None:
        try:
            with winreg.OpenKey(
                winreg.HKEY_CURRENT_USER, RUN_KEY_PATH, 0, winreg.KEY_SET_VALUE,
            ) as key:
                winreg.DeleteValue(key, VALUE_NAME)
        except FileNotFoundError:
            pass
        except OSError:
            raise
