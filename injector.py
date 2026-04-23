"""Text injection.

Two paths:

1. Console windows (cmd.exe, PowerShell, Windows Terminal) — type each
   character via SendInput with KEYEVENTF_UNICODE. conhost's cooked-mode
   input reads these as normal typed chars, so this works regardless of
   whether "Use Ctrl+Shift+C/V as Copy/Paste" is enabled. Ctrl+V paste is
   unreliable in legacy conhost (it's just unbound when that setting is off),
   which is why simulated paste silently no-ops in plain cmd.

2. Everywhere else — save clipboard, overwrite, simulate Ctrl+V, restore.
   Fast and atomic in normal GUI apps.

Console detection uses the window class of the foreground HWND. If detection
fails for any reason, we fall back to clipboard paste — historical behaviour.
"""
from __future__ import annotations

import ctypes
import time
from ctypes import wintypes

import keyboard
import pyperclip

import debug_log
import elevation


class ElevationRequired(Exception):
    """Raised when SendInput is rejected because the target window is at a
    higher integrity level than this process. The only fix is to relaunch
    FlowClone as administrator."""


# ---------------------------------------------------------------------------
# Win32 bindings (kept private — this module never leaks them)
# ---------------------------------------------------------------------------

_user32 = ctypes.WinDLL("user32", use_last_error=True)


class _KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", wintypes.WORD),
        ("wScan", wintypes.WORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.c_void_p),
    ]


class _INPUT_UNION(ctypes.Union):
    _fields_ = [("ki", _KEYBDINPUT)]


class _INPUT(ctypes.Structure):
    _fields_ = [
        ("type", wintypes.DWORD),
        ("ii", _INPUT_UNION),
    ]


_INPUT_KEYBOARD = 1
_KEYEVENTF_UNICODE = 0x0004
_KEYEVENTF_KEYUP = 0x0002
_VK_RETURN = 0x0D

_user32.SendInput.argtypes = [wintypes.UINT, ctypes.POINTER(_INPUT), ctypes.c_int]
_user32.SendInput.restype = wintypes.UINT
_user32.GetForegroundWindow.restype = wintypes.HWND
_user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
_user32.GetClassNameW.restype = ctypes.c_int


# Window classes that indicate a console target.
_CONSOLE_CLASSES = {
    "ConsoleWindowClass",              # classic conhost: cmd.exe, powershell.exe
    "CASCADIA_HOSTING_WINDOW_CLASS",   # Windows Terminal (older builds)
    "WindowsTerminal",                 # Windows Terminal (newer builds)
    "PseudoConsoleWindow",             # rare; some embedded conhost variants
}


def _foreground_window_class() -> str:
    hwnd = _user32.GetForegroundWindow()
    if not hwnd:
        return ""
    buf = ctypes.create_unicode_buffer(256)
    if _user32.GetClassNameW(hwnd, buf, 256) == 0:
        return ""
    return buf.value


def _is_foreground_console() -> bool:
    return _foreground_window_class() in _CONSOLE_CLASSES


def _send_inputs(*inputs: _INPUT) -> int:
    """Returns the number of events actually inserted. On UIPI block
    (non-elevated process sending to elevated window) this is 0 with
    GetLastError == 5 (ERROR_ACCESS_DENIED)."""
    arr = (_INPUT * len(inputs))(*inputs)
    ctypes.set_last_error(0)
    n = _user32.SendInput(len(inputs), arr, ctypes.sizeof(_INPUT))
    return n


def _unicode_pair(code: int) -> tuple[_INPUT, _INPUT]:
    down = _INPUT(
        type=_INPUT_KEYBOARD,
        ii=_INPUT_UNION(ki=_KEYBDINPUT(
            wVk=0, wScan=code, dwFlags=_KEYEVENTF_UNICODE, time=0, dwExtraInfo=None,
        )),
    )
    up = _INPUT(
        type=_INPUT_KEYBOARD,
        ii=_INPUT_UNION(ki=_KEYBDINPUT(
            wVk=0, wScan=code, dwFlags=_KEYEVENTF_UNICODE | _KEYEVENTF_KEYUP,
            time=0, dwExtraInfo=None,
        )),
    )
    return down, up


def _vk_pair(vk: int) -> tuple[_INPUT, _INPUT]:
    down = _INPUT(
        type=_INPUT_KEYBOARD,
        ii=_INPUT_UNION(ki=_KEYBDINPUT(
            wVk=vk, wScan=0, dwFlags=0, time=0, dwExtraInfo=None,
        )),
    )
    up = _INPUT(
        type=_INPUT_KEYBOARD,
        ii=_INPUT_UNION(ki=_KEYBDINPUT(
            wVk=vk, wScan=0, dwFlags=_KEYEVENTF_KEYUP, time=0, dwExtraInfo=None,
        )),
    )
    return down, up


def _type_unicode_char(ch: str) -> None:
    cp = ord(ch)
    if cp > 0xFFFF:
        # Surrogate pair for astral plane codepoints (emoji, etc.)
        cp -= 0x10000
        _send_inputs(*_unicode_pair(0xD800 + (cp >> 10)))
        _send_inputs(*_unicode_pair(0xDC00 + (cp & 0x3FF)))
    else:
        _send_inputs(*_unicode_pair(cp))


def _type_text(text: str) -> tuple[int, int]:
    """Type `text` char-by-char via synthetic Unicode keyboard events.
    Returns (events_requested, events_inserted) — if they differ, Windows
    rejected some events (commonly UIPI: non-admin process → admin target)."""
    normalised = text.replace("\r\n", "\n").replace("\r", "\n")
    requested = 0
    inserted = 0
    for ch in normalised:
        if ch == "\n":
            n = _send_inputs(*_vk_pair(_VK_RETURN))
            requested += 2
            inserted += n
        else:
            cp = ord(ch)
            if cp > 0xFFFF:
                cp -= 0x10000
                inserted += _send_inputs(*_unicode_pair(0xD800 + (cp >> 10)))
                inserted += _send_inputs(*_unicode_pair(0xDC00 + (cp & 0x3FF)))
                requested += 4
            else:
                inserted += _send_inputs(*_unicode_pair(cp))
                requested += 2
    return requested, inserted


def _paste_via_clipboard(text: str) -> None:
    try:
        original = pyperclip.paste()
    except Exception:
        original = ""

    pyperclip.copy(text)
    time.sleep(0.05)
    keyboard.send("ctrl+v")
    time.sleep(0.12)

    try:
        pyperclip.copy(original)
    except Exception:
        pass


def inject_text(text: str):
    """Inject `text` into the foreground window.

    Raises ElevationRequired if the target window blocks us via UIPI
    (non-admin FlowClone → admin cmd/PowerShell/Terminal). Callers should
    catch that and surface a clear overlay message — the only real fix is
    to relaunch FlowClone as administrator.
    """
    if not text or not text.strip():
        return

    fg_class = _foreground_window_class()
    is_console = fg_class in _CONSOLE_CLASSES
    debug_log.log(
        "inject.start",
        chars=len(text),
        fg_class=fg_class,
        path="type" if is_console else "clipboard",
        elevated=elevation.is_elevated(),
    )

    if is_console:
        try:
            requested, inserted = _type_text(text)
            debug_log.log(
                "inject.type_done",
                requested=requested,
                inserted=inserted,
                last_err=ctypes.get_last_error(),
            )
            if inserted == 0 and requested > 0:
                # Zero inserted = UIPI block (non-admin → admin window).
                # Clipboard paste hits the same wall — Ctrl+V is also
                # UIPI-gated — so we signal the caller instead of silently
                # trying a fallback that will also fail.
                if not elevation.is_elevated():
                    raise ElevationRequired(
                        "Target window is elevated — run FlowClone as admin"
                    )
                # We ARE elevated and still 0 inserted — genuinely unusual;
                # fall through to clipboard as a last-ditch try.
                debug_log.log("inject.zero_inserted_elevated_fallback")
            else:
                return
        except ElevationRequired:
            raise
        except Exception as e:
            debug_log.log("inject.type_exc", error=str(e))

    _paste_via_clipboard(text)
    debug_log.log("inject.clipboard_done")


def copy_selection() -> str:
    """Simulate Ctrl+C and read the clipboard.

    In classic cmd.exe, Ctrl+C is SIGINT rather than copy, so Command Mode
    against a cmd selection will return empty here — that's a known
    limitation of the console subsystem, not a bug in this function.
    Returns selected text or empty string.
    """
    try:
        before = pyperclip.paste()
    except Exception:
        before = ""

    pyperclip.copy("")
    time.sleep(0.05)
    keyboard.send("ctrl+c")
    time.sleep(0.15)

    try:
        selected = pyperclip.paste()
    except Exception:
        selected = ""

    try:
        pyperclip.copy(before)
    except Exception:
        pass

    return selected or ""
