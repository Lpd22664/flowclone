"""Opt-in file logging for diagnosing hotkey / overlay issues.

Disabled by default (zero overhead). Enable with environment variable
FLOWCLONE_DEBUG=1. Log lives at %APPDATA%\\FlowClone\\debug.log (or next to
the source files in dev). Rotates at ~256 KiB to stay small.

Exists specifically for tricky Windows interaction bugs that only reproduce
on a user's machine (e.g. push-to-talk not working while a console window
is focused) — we can ask them to set the env var, reproduce, and ship the
log back.
"""
from __future__ import annotations

import ctypes
import os
import sys
import threading
import time
from ctypes import wintypes
from pathlib import Path

from config import _app_dir


_ENABLED = os.environ.get("FLOWCLONE_DEBUG", "") not in ("", "0", "false", "False")
_MAX_SIZE = 256 * 1024  # 256 KiB — enough for a few hundred events
_LOCK = threading.Lock()


def _log_path() -> Path:
    return Path(_app_dir()) / "debug.log"


def is_enabled() -> bool:
    return _ENABLED


def _foreground_window_info() -> str:
    """Best-effort foreground HWND class + title. Silent on any failure."""
    if sys.platform != "win32":
        return ""
    try:
        u = ctypes.WinDLL("user32", use_last_error=True)
        u.GetForegroundWindow.restype = wintypes.HWND
        u.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
        u.GetClassNameW.restype = ctypes.c_int
        u.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
        u.GetWindowTextW.restype = ctypes.c_int

        hwnd = u.GetForegroundWindow()
        if not hwnd:
            return "fg=none"
        cls = ctypes.create_unicode_buffer(256)
        u.GetClassNameW(hwnd, cls, 256)
        title = ctypes.create_unicode_buffer(256)
        u.GetWindowTextW(hwnd, title, 256)
        return f"fg_class={cls.value!r} fg_title={title.value!r}"
    except Exception:
        return "fg=error"


def log(event: str, **fields) -> None:
    """Append one line to debug.log. No-op when disabled. Never raises."""
    if not _ENABLED:
        return
    try:
        ts = time.strftime("%H:%M:%S", time.localtime())
        parts = [ts, event]
        for k, v in fields.items():
            parts.append(f"{k}={v!r}")
        parts.append(_foreground_window_info())
        line = " ".join(parts) + "\n"

        with _LOCK:
            path = _log_path()
            # Rotate if too big — keep last half to preserve recent context.
            try:
                if path.exists() and path.stat().st_size > _MAX_SIZE:
                    with open(path, "rb") as f:
                        f.seek(-_MAX_SIZE // 2, 2)
                        tail = f.read()
                    with open(path, "wb") as f:
                        f.write(b"--- rotated ---\n")
                        f.write(tail)
            except Exception:
                pass
            with open(path, "a", encoding="utf-8") as f:
                f.write(line)
    except Exception:
        # Logging must never break the app.
        pass


if _ENABLED:
    log("debug_enabled", pid=os.getpid(), frozen=bool(getattr(sys, "frozen", False)))
