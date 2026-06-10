"""Text injection and selection capture."""
from __future__ import annotations

import sys
import time

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
    paste_combo = "ctrl+shift+v" if sys.platform != "win32" else "ctrl+v"
    _send_hotkey(paste_combo)
    time.sleep(0.12)
    try:
        _clipboard_set(original)
    except Exception:
        pass


if sys.platform == "win32":
    import keyboard
    import ctypes
    from ctypes import wintypes

    def _send_hotkey(combo: str) -> None:
        keyboard.send(combo)

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
    import atexit
    import fcntl
    import os
    import struct

    _EV_SYN = 0x00
    _EV_KEY = 0x01
    _SYN_REPORT = 0
    _KEY_LEFTCTRL = 29
    _KEY_LEFTSHIFT = 42
    _KEY_C = 46
    _KEY_V = 47

    def _ioc(direction: int, type_: int, nr: int, size: int) -> int:
        return (direction << 30) | (size << 16) | (type_ << 8) | nr

    def _io(type_: int, nr: int) -> int:
        return (type_ << 8) | nr

    def _iow(type_: int, nr: int, size: int) -> int:
        return _ioc(1, type_, nr, size)

    _UINPUT = ord("U")
    _UI_DEV_CREATE = _io(_UINPUT, 1)
    _UI_DEV_DESTROY = _io(_UINPUT, 2)
    _UI_SET_EVBIT = _iow(_UINPUT, 100, struct.calcsize("i"))
    _UI_SET_KEYBIT = _iow(_UINPUT, 101, struct.calcsize("i"))

    class _UInputKeyboard:
        def __init__(self):
            self._fd: int | None = None

        def _open(self) -> int:
            if self._fd is not None:
                return self._fd

            fd = os.open("/dev/uinput", os.O_WRONLY | os.O_NONBLOCK)
            try:
                for ev in (_EV_KEY,):
                    fcntl.ioctl(fd, _UI_SET_EVBIT, ev)
                for key in (_KEY_LEFTCTRL, _KEY_LEFTSHIFT, _KEY_C, _KEY_V):
                    fcntl.ioctl(fd, _UI_SET_KEYBIT, key)

                # Legacy uinput_user_dev layout: name[80], input_id, ff count,
                # then four ABS arrays. It is stable and avoids ctypes structs.
                name = b"FlowClone virtual keyboard"
                user_dev = struct.pack(
                    "80sHHHHI" + "i" * 64 * 4,
                    name,
                    0x03,  # BUS_USB
                    0xF10C,
                    0x0001,
                    0x0001,
                    0,
                    *([0] * 64 * 4),
                )
                os.write(fd, user_dev)
                fcntl.ioctl(fd, _UI_DEV_CREATE)
                time.sleep(0.15)
            except Exception:
                os.close(fd)
                raise

            self._fd = fd
            return fd

        def close(self):
            if self._fd is None:
                return
            try:
                fcntl.ioctl(self._fd, _UI_DEV_DESTROY)
            except Exception:
                pass
            try:
                os.close(self._fd)
            except Exception:
                pass
            self._fd = None

        def _event(self, ev_type: int, code: int, value: int):
            fd = self._open()
            os.write(fd, struct.pack("llHHI", 0, 0, ev_type, code, value))

        def _sync(self):
            self._event(_EV_SYN, _SYN_REPORT, 0)

        def hotkey(self, key: int, *, shift: bool = False):
            self._event(_EV_KEY, _KEY_LEFTCTRL, 1)
            if shift:
                self._event(_EV_KEY, _KEY_LEFTSHIFT, 1)
            self._event(_EV_KEY, key, 1)
            self._sync()
            self._event(_EV_KEY, key, 0)
            if shift:
                self._event(_EV_KEY, _KEY_LEFTSHIFT, 0)
            self._event(_EV_KEY, _KEY_LEFTCTRL, 0)
            self._sync()

    _uinput_keyboard = _UInputKeyboard()
    atexit.register(_uinput_keyboard.close)

    def _send_hotkey(combo: str) -> None:
        """Send a simple Ctrl+key chord on Linux.

        Prefer uinput because native Wayland apps ignore XTest events. Fall
        back to XTest for systems where /dev/uinput is unavailable.
        """
        key_for_combo = {
            "ctrl+c": (_KEY_C, False),
            "control+c": (_KEY_C, False),
            "ctrl+v": (_KEY_V, False),
            "control+v": (_KEY_V, False),
            "ctrl+shift+v": (_KEY_V, True),
            "control+shift+v": (_KEY_V, True),
        }
        normalised = "+".join(p.strip().lower() for p in combo.split("+") if p.strip())
        key_spec = key_for_combo.get(normalised)
        if key_spec is not None:
            key_code, shift = key_spec
            try:
                _uinput_keyboard.hotkey(key_code, shift=shift)
                return
            except Exception as e:
                debug_log.log("inject.uinput_failed", combo=combo, error=str(e))

        try:
            from Xlib import X, XK, display
            from Xlib.ext import xtest
        except Exception as e:
            raise RuntimeError(f"Linux key injection unavailable: {e}") from e

        parts = [p.strip().lower() for p in combo.split("+") if p.strip()]
        if len(parts) not in (2, 3) or parts[0] not in ("ctrl", "control"):
            raise RuntimeError(f"Unsupported Linux hotkey: {combo}")

        d = display.Display()
        try:
            ctrl = d.keysym_to_keycode(XK.XK_Control_L)
            shift = d.keysym_to_keycode(XK.XK_Shift_L) if len(parts) == 3 else 0
            key_name = parts[-1]
            key = d.keysym_to_keycode(XK.string_to_keysym(key_name))
            if not ctrl or not key:
                raise RuntimeError(f"Could not resolve Linux hotkey: {combo}")

            xtest.fake_input(d, X.KeyPress, ctrl)
            if shift:
                xtest.fake_input(d, X.KeyPress, shift)
            xtest.fake_input(d, X.KeyPress, key)
            xtest.fake_input(d, X.KeyRelease, key)
            if shift:
                xtest.fake_input(d, X.KeyRelease, shift)
            xtest.fake_input(d, X.KeyRelease, ctrl)
            d.sync()
        finally:
            d.close()

    def inject_text(text: str):
        """Paste text into the active Linux application.

        This uses the desktop clipboard plus an XTest synthetic Ctrl+V. XTest
        works without root under FlowClone's XWayland/XCB runtime.
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
    _send_hotkey("ctrl+c")
    time.sleep(0.15)

    selected = _clipboard_get()
    try:
        _clipboard_set(before)
    except Exception:
        pass

    return selected or ""
