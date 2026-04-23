"""Raw Windows low-level keyboard hook.

Why reimplement instead of using `keyboard`?

The `keyboard` library (0.13.5) has a stateful AltGr compensation filter
(_winkeyboard.py:502-505) — when it sees the phantom LCtrl that Windows
synthesises alongside Right Alt on AltGr-capable layouts, it sets a
module-level flag and silently drops the next Right Alt event by returning
early without invoking user callbacks. If that flag ever desyncs — e.g.
because a focus change or a per-window input-layout switch causes Windows
to emit the phantom LCtrl without a matching user-pressed Right Alt, or
vice versa — Right Alt can get swallowed indefinitely in certain foreground
contexts. This appears to be the root cause of "PTT works everywhere except
cmd/PowerShell/Windows Terminal" reports: switching focus into a console
window seems to emit the LCtrl pattern the library is watching for.

This module installs WH_KEYBOARD_LL directly via ctypes on a dedicated
background thread with its own message pump. No AltGr rewriting, no event
filtering beyond the one piece that matters (don't treat our own injected
Unicode chars as hotkey presses — those set LLKHF_INJECTED | LLKHF_ALTDOWN,
same as the real keyboard lib filter).

Public API — a deliberately small surface:
    register_key(vk_code, on_press, on_release)   returns a token
    unregister(token)
    start() / stop()  (idempotent)

Key identification is by Windows virtual-key code (vk_code) because scan
codes vary by keyboard layout but VKs are stable. See VK_* constants below
for the ones FlowClone cares about.
"""
from __future__ import annotations

import ctypes
import itertools
import threading
import time
from ctypes import wintypes
from typing import Callable, Dict, Optional

import debug_log


# --- Win32 bindings ----------------------------------------------------

_user32 = ctypes.WinDLL("user32", use_last_error=True)
_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

_WH_KEYBOARD_LL = 13
_WM_KEYDOWN = 0x0100
_WM_KEYUP = 0x0101
_WM_SYSKEYDOWN = 0x0104
_WM_SYSKEYUP = 0x0105
_WM_QUIT = 0x0012

_LLKHF_INJECTED = 0x10
_LLKHF_ALTDOWN = 0x20

_VK_PACKET = 0xE7     # virtual-key code assigned to SendInput KEYEVENTF_UNICODE events


class _KBDLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [
        ("vkCode", wintypes.DWORD),
        ("scanCode", wintypes.DWORD),
        ("flags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.c_void_p),
    ]


_LowLevelKeyboardProc = ctypes.WINFUNCTYPE(
    ctypes.c_long, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM,
)

_user32.SetWindowsHookExW.argtypes = [
    ctypes.c_int, _LowLevelKeyboardProc, wintypes.HINSTANCE, wintypes.DWORD,
]
_user32.SetWindowsHookExW.restype = wintypes.HHOOK
_user32.UnhookWindowsHookEx.argtypes = [wintypes.HHOOK]
_user32.UnhookWindowsHookEx.restype = wintypes.BOOL
_user32.CallNextHookEx.argtypes = [
    wintypes.HHOOK, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM,
]
_user32.CallNextHookEx.restype = wintypes.LPARAM

_user32.GetMessageW.argtypes = [
    wintypes.LPMSG, wintypes.HWND, wintypes.UINT, wintypes.UINT,
]
_user32.GetMessageW.restype = wintypes.BOOL
_user32.TranslateMessage.argtypes = [wintypes.LPMSG]
_user32.DispatchMessageW.argtypes = [wintypes.LPMSG]
_user32.PostThreadMessageW.argtypes = [
    wintypes.DWORD, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM,
]
_user32.PostThreadMessageW.restype = wintypes.BOOL

_kernel32.GetCurrentThreadId.restype = wintypes.DWORD
_kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
_kernel32.GetModuleHandleW.restype = wintypes.HMODULE


# --- Registration state ------------------------------------------------

class Handler:
    """One registered key. Tracks down-state so we don't fire press repeatedly
    under OS key-repeat, and only fire release if we saw the press."""

    def __init__(
        self,
        vk_code: int,
        on_press: Optional[Callable[[], None]],
        on_release: Optional[Callable[[], None]],
    ):
        self.vk_code = vk_code
        self.on_press = on_press
        self.on_release = on_release
        self.is_down = False


_handlers: Dict[int, Handler] = {}          # token -> Handler
_by_vk: Dict[int, Handler] = {}             # vk -> Handler (first wins)
_state_lock = threading.Lock()
_token_source = itertools.count(1)


# --- Hook thread -------------------------------------------------------

_thread: Optional[threading.Thread] = None
_thread_id: int = 0
_hook_handle: Optional[int] = None
_ready = threading.Event()
_stopping = threading.Event()

# Strong reference to the ctypes callback; must outlive the hook or the
# trampoline gets GC'd and Windows calls into freed memory.
_proc_ref = None


def _is_self_injected_unicode(flags: int) -> bool:
    """Mirror keyboard lib's filter: events we or another tool generated via
    SendInput with KEYEVENTF_UNICODE have both INJECTED and ALTDOWN set."""
    return (flags & (_LLKHF_INJECTED | _LLKHF_ALTDOWN)) == (_LLKHF_INJECTED | _LLKHF_ALTDOWN)


def _hook_proc(nCode, wParam, lParam):
    if nCode < 0:
        return _user32.CallNextHookEx(None, nCode, wParam, lParam)

    try:
        kb = ctypes.cast(lParam, ctypes.POINTER(_KBDLLHOOKSTRUCT)).contents
        vk = kb.vkCode
        flags = kb.flags

        # Skip our own SendInput-KEYEVENTF_UNICODE chars from injector.py.
        # They arrive as VK_PACKET (0xE7) or with INJECTED|ALTDOWN both set;
        # either signal is enough to identify them.
        if vk == _VK_PACKET or _is_self_injected_unicode(flags):
            return _user32.CallNextHookEx(None, nCode, wParam, lParam)

        handler = _by_vk.get(vk)
        if handler is not None:
            is_down = wParam in (_WM_KEYDOWN, _WM_SYSKEYDOWN)
            is_up = wParam in (_WM_KEYUP, _WM_SYSKEYUP)

            if is_down and not handler.is_down:
                handler.is_down = True
                debug_log.log("keyhook.press", vk=hex(vk), scan=kb.scanCode, flags=hex(flags))
                cb = handler.on_press
                if cb is not None:
                    # Dispatch off the hook thread; LL hooks have a ~300 ms
                    # budget and Windows disables them silently if exceeded.
                    threading.Thread(target=_safe, args=(cb,), daemon=True).start()
            elif is_up and handler.is_down:
                handler.is_down = False
                debug_log.log("keyhook.release", vk=hex(vk))
                cb = handler.on_release
                if cb is not None:
                    threading.Thread(target=_safe, args=(cb,), daemon=True).start()
    except Exception:
        pass

    return _user32.CallNextHookEx(None, nCode, wParam, lParam)


def _safe(fn: Callable[[], None]):
    try:
        fn()
    except Exception:
        pass


def _thread_main():
    global _thread_id, _hook_handle, _proc_ref

    _thread_id = _kernel32.GetCurrentThreadId()

    _proc_ref = _LowLevelKeyboardProc(_hook_proc)
    hmod = _kernel32.GetModuleHandleW(None)
    _hook_handle = _user32.SetWindowsHookExW(
        _WH_KEYBOARD_LL, _proc_ref, hmod, 0,
    )
    if not _hook_handle:
        err = ctypes.get_last_error()
        debug_log.log("keyhook.install_failed", err=err)
        _ready.set()
        return

    debug_log.log("keyhook.installed", handle=_hook_handle, tid=_thread_id)
    _ready.set()

    msg = wintypes.MSG()
    while not _stopping.is_set():
        r = _user32.GetMessageW(ctypes.byref(msg), 0, 0, 0)
        if r == 0 or r == -1:
            break
        _user32.TranslateMessage(ctypes.byref(msg))
        _user32.DispatchMessageW(ctypes.byref(msg))

    try:
        _user32.UnhookWindowsHookEx(_hook_handle)
    except Exception:
        pass
    _hook_handle = None
    debug_log.log("keyhook.stopped")


# --- Public API --------------------------------------------------------

def start() -> bool:
    """Install the hook on its dedicated thread. Idempotent. Returns True
    once the hook is installed and firing."""
    global _thread
    with _state_lock:
        if _thread is not None and _thread.is_alive():
            return bool(_hook_handle)
        _ready.clear()
        _stopping.clear()
        _thread = threading.Thread(
            target=_thread_main, name="keyhook", daemon=True,
        )
        _thread.start()
    _ready.wait(timeout=2.0)
    return bool(_hook_handle)


def stop() -> None:
    """Unhook and stop the thread. Idempotent."""
    global _thread
    with _state_lock:
        t = _thread
        if t is None or not t.is_alive():
            _thread = None
            return
        _stopping.set()
        if _thread_id:
            _user32.PostThreadMessageW(_thread_id, _WM_QUIT, 0, 0)
    t.join(timeout=2.0)
    _thread = None


def register_key(
    vk_code: int,
    on_press: Optional[Callable[[], None]],
    on_release: Optional[Callable[[], None]] = None,
) -> int:
    """Register press/release callbacks for a single VK. Returns a token to
    pass to unregister(). If the same VK is already registered, the new
    registration replaces it."""
    token = next(_token_source)
    h = Handler(vk_code, on_press, on_release)
    with _state_lock:
        # Replace existing registration for this VK — the caller wins.
        for tk, existing in list(_handlers.items()):
            if existing.vk_code == vk_code:
                _handlers.pop(tk, None)
        _handlers[token] = h
        _by_vk[vk_code] = h
    debug_log.log("keyhook.register_key", vk=hex(vk_code), token=token)
    return token


def unregister(token: int) -> None:
    """Remove a prior registration. Silent if token is unknown."""
    with _state_lock:
        h = _handlers.pop(token, None)
        if h is not None and _by_vk.get(h.vk_code) is h:
            _by_vk.pop(h.vk_code, None)
    if h is not None:
        debug_log.log("keyhook.unregister", vk=hex(h.vk_code), token=token)


def is_installed() -> bool:
    return bool(_hook_handle)


# --- VK constants FlowClone uses ---------------------------------------

VK_LMENU = 0xA4
VK_RMENU = 0xA5
VK_MENU = 0x12    # generic Alt, mostly useful as a fallback
VK_LCONTROL = 0xA2
VK_RCONTROL = 0xA3

# Mapping of friendly names to VK, so settings can persist the human label
# ("right alt") without knowing VK values.
NAME_TO_VK = {
    "right alt": VK_RMENU,
    "alt gr":    VK_RMENU,
    "left alt":  VK_LMENU,
    "alt":       VK_LMENU,        # prefer LAlt for ambiguous "alt"
    "right ctrl": VK_RCONTROL,
    "left ctrl":  VK_LCONTROL,
    "ctrl":       VK_LCONTROL,
}


def vk_for_name(name: str) -> Optional[int]:
    """Resolve a friendly hotkey name (e.g. 'right alt') to its VK code."""
    return NAME_TO_VK.get((name or "").strip().lower())
