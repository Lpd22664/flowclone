"""Text injection and selection capture."""
from __future__ import annotations

import sys
import time

import keyboard
import pyperclip

import debug_log
import elevation


class ElevationRequired(Exception):
    """Raised when Windows UIPI blocks SendInput into an elevated target."""


def _clipboard_get() -> str:
    try:
        return pyperclip.paste()
    except Exception:
        try:
            from PyQt6.QtGui import QGuiApplication

            cb = QGuiApplication.clipboard()
            return cb.text() if cb is not None else ""
        except Exception:
            return ""


def _clipboard_set(text: str) -> None:
    try:
        pyperclip.copy(text)
        return
    except Exception:
        pass

    try:
        from PyQt6.QtGui import QGuiApplication

        cb = QGuiApplication.clipboard()
        if cb is not None:
            cb.setText(text)
    except Exception as e:
        raise RuntimeError(f"Clipboard unavailable: {e}") from e


def _paste_via_clipboard(text: str) -> None:
    original = _clipboard_get()
    _clipboard_set(text)
    time.sleep(0.05)
    keyboard.send("ctrl+v")
    time.sleep(0.12)
    try:
        _clipboard_set(original)
    except Exception:
        pass


if sys.platform == "win32":
    import ctypes
    from ctypes import wintypes

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

    _user32.SendInput.argtypes = [
        wintypes.UINT, ctypes.POINTER(_INPUT), ctypes.c_int,
    ]
    _user32.SendInput.restype = wintypes.UINT
    _user32.GetForegroundWindow.restype = wintypes.HWND
    _user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    _user32.GetClassNameW.restype = ctypes.c_int

    _CONSOLE_CLASSES = {
        "ConsoleWindowClass",
        "CASCADIA_HOSTING_WINDOW_CLASS",
        "WindowsTerminal",
        "PseudoConsoleWindow",
    }

    def _foreground_window_class() -> str:
        hwnd = _user32.GetForegroundWindow()
        if not hwnd:
            return ""
        buf = ctypes.create_unicode_buffer(256)
        if _user32.GetClassNameW(hwnd, buf, 256) == 0:
            return ""
        return buf.value


    def _send_inputs(*inputs: _INPUT) -> int:
        arr = (_INPUT * len(inputs))(*inputs)
        ctypes.set_last_error(0)
        return _user32.SendInput(len(inputs), arr, ctypes.sizeof(_INPUT))


    def _unicode_pair(code: int) -> tuple[_INPUT, _INPUT]:
        down = _INPUT(
            type=_INPUT_KEYBOARD,
            ii=_INPUT_UNION(ki=_KEYBDINPUT(
                wVk=0, wScan=code, dwFlags=_KEYEVENTF_UNICODE,
                time=0, dwExtraInfo=None,
            )),
        )
        up = _INPUT(
            type=_INPUT_KEYBOARD,
            ii=_INPUT_UNION(ki=_KEYBDINPUT(
                wVk=0, wScan=code,
                dwFlags=_KEYEVENTF_UNICODE | _KEYEVENTF_KEYUP,
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
                wVk=vk, wScan=0, dwFlags=_KEYEVENTF_KEYUP,
                time=0, dwExtraInfo=None,
            )),
        )
        return down, up


    def _type_text(text: str) -> tuple[int, int]:
        normalised = text.replace("\r\n", "\n").replace("\r", "\n")
        requested = 0
        inserted = 0
        for ch in normalised:
            if ch == "\n":
                inserted += _send_inputs(*_vk_pair(_VK_RETURN))
                requested += 2
                continue

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


    def inject_text(text: str):
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
                    if not elevation.is_elevated():
                        raise ElevationRequired(
                            "Target window is elevated - run FlowClone as admin"
                        )
                    debug_log.log("inject.zero_inserted_elevated_fallback")
                else:
                    return
            except ElevationRequired:
                raise
            except Exception as e:
                debug_log.log("inject.type_exc", error=str(e))

        _paste_via_clipboard(text)
        debug_log.log("inject.clipboard_done")


else:

    def inject_text(text: str):
        """Paste text into the active Linux application.

        This uses the desktop clipboard plus a synthetic Ctrl+V. On Wayland,
        the compositor may reject synthetic input unless the `keyboard`
        backend has permission to access evdev/uinput.
        """
        if not text or not text.strip():
            return
        debug_log.log("inject.start_linux", chars=len(text))
        _paste_via_clipboard(text)
        debug_log.log("inject.clipboard_done_linux")


def copy_selection() -> str:
    """Simulate Ctrl+C and read the clipboard."""
    before = _clipboard_get()

    _clipboard_set("")
    time.sleep(0.05)
    keyboard.send("ctrl+c")
    time.sleep(0.15)

    selected = _clipboard_get()
    try:
        _clipboard_set(before)
    except Exception:
        pass

    return selected or ""
