"""Linux global keyboard hook via evdev.

Linux counterpart of `keyhook.py` (Windows WH_KEYBOARD_LL). Reads key events
straight from /dev/input/event* so it works under both X11 and Wayland —
Wayland compositors don't let ordinary clients observe global key events.

The `keyboard` library is not used on Linux because it requires root. evdev
only needs read access to the input devices, which the user gets by being in
the `input` group (or via a logind/udev ACL).

Devices are never grabbed — events still reach the focused application.
Our own uinput virtual keyboard (see injector_linux.py) is ignored so that
synthetic Ctrl+V / Ctrl+C never trigger hotkeys.

Public API mirrors keyhook.py, plus combo-hotkey helpers that replace
`keyboard.add_hotkey` / `keyboard.read_hotkey` on Linux:
    register_key(code, on_press, on_release)  -> token
    unregister(token)
    add_hotkey(combo, callback)                -> token
    remove_hotkey(token)
    read_hotkey()                              -> str (blocking)
    vk_for_name(name)                          -> evdev key code
    start() / stop() / is_installed()
"""

from __future__ import annotations

import itertools
import select
import threading
import time
from typing import Callable, Dict, List, Optional, Set, Tuple

import evdev
from evdev import ecodes as _e

import debug_log


VIRTUAL_DEVICE_NAME = "flowclone-virtual-keyboard"
_RESCAN_INTERVAL = 3.0  # seconds between checks for hot-plugged keyboards


# --- Key names ---------------------------------------------------------

# Left-hand modifiers get the generic name, right-hand ones are explicit.
# Matches the `keyboard` library's naming closely enough that config files
# written on Windows ("right alt", "ctrl+shift+space") work unchanged.
_MODIFIER_CODE_TO_NAME = {
    _e.KEY_LEFTCTRL: "ctrl",
    _e.KEY_RIGHTCTRL: "right ctrl",
    _e.KEY_LEFTSHIFT: "shift",
    _e.KEY_RIGHTSHIFT: "right shift",
    _e.KEY_LEFTALT: "alt",
    _e.KEY_RIGHTALT: "right alt",
    _e.KEY_LEFTMETA: "windows",
    _e.KEY_RIGHTMETA: "right windows",
}

_CTRL = (_e.KEY_LEFTCTRL, _e.KEY_RIGHTCTRL)
_SHIFT = (_e.KEY_LEFTSHIFT, _e.KEY_RIGHTSHIFT)
_ALT = (_e.KEY_LEFTALT, _e.KEY_RIGHTALT)
_META = (_e.KEY_LEFTMETA, _e.KEY_RIGHTMETA)

_NAME_TO_CODES: Dict[str, Tuple[int, ...]] = {
    "ctrl": _CTRL,
    "control": _CTRL,
    "left ctrl": (_e.KEY_LEFTCTRL,),
    "right ctrl": (_e.KEY_RIGHTCTRL,),
    "shift": _SHIFT,
    "left shift": (_e.KEY_LEFTSHIFT,),
    "right shift": (_e.KEY_RIGHTSHIFT,),
    # "alt" prefers LAlt when used as a single PTT key (first entry), but
    # matches either Alt inside a combo — same as on Windows.
    "alt": _ALT,
    "left alt": (_e.KEY_LEFTALT,),
    "right alt": (_e.KEY_RIGHTALT,),
    "alt gr": (_e.KEY_RIGHTALT,),
    "altgr": (_e.KEY_RIGHTALT,),
    "windows": _META,
    "win": _META,
    "super": _META,
    "meta": _META,
    "cmd": _META,
    "command": _META,
    "left windows": (_e.KEY_LEFTMETA,),
    "right windows": (_e.KEY_RIGHTMETA,),
}

_SIMPLE_KEYS: Dict[str, int] = {
    "space": _e.KEY_SPACE,
    "enter": _e.KEY_ENTER,
    "tab": _e.KEY_TAB,
    "esc": _e.KEY_ESC,
    "backspace": _e.KEY_BACKSPACE,
    "delete": _e.KEY_DELETE,
    "insert": _e.KEY_INSERT,
    "home": _e.KEY_HOME,
    "end": _e.KEY_END,
    "page up": _e.KEY_PAGEUP,
    "page down": _e.KEY_PAGEDOWN,
    "up": _e.KEY_UP,
    "down": _e.KEY_DOWN,
    "left": _e.KEY_LEFT,
    "right": _e.KEY_RIGHT,
    "caps lock": _e.KEY_CAPSLOCK,
    "print screen": _e.KEY_SYSRQ,
    "menu": _e.KEY_COMPOSE,
    "-": _e.KEY_MINUS,
    "=": _e.KEY_EQUAL,
    ",": _e.KEY_COMMA,
    ".": _e.KEY_DOT,
    "/": _e.KEY_SLASH,
    ";": _e.KEY_SEMICOLON,
    "'": _e.KEY_APOSTROPHE,
    "[": _e.KEY_LEFTBRACE,
    "]": _e.KEY_RIGHTBRACE,
    "\\": _e.KEY_BACKSLASH,
    "`": _e.KEY_GRAVE,
}
for _ch in "abcdefghijklmnopqrstuvwxyz0123456789":
    _SIMPLE_KEYS[_ch] = _e.ecodes["KEY_" + _ch.upper()]
for _n in range(1, 25):
    _SIMPLE_KEYS[f"f{_n}"] = _e.ecodes[f"KEY_F{_n}"]

_ALIASES = {
    "return": "enter",
    "escape": "esc",
    "del": "delete",
    "ins": "insert",
    "pageup": "page up",
    "pagedown": "page down",
    "capslock": "caps lock",
    "spacebar": "space",
}

_CODE_TO_SIMPLE_NAME = {code: name for name, code in _SIMPLE_KEYS.items()}


def _codes_for_name(name: str) -> Optional[Tuple[int, ...]]:
    n = (name or "").strip().lower()
    n = _ALIASES.get(n, n)
    if n in _NAME_TO_CODES:
        return _NAME_TO_CODES[n]
    if n in _SIMPLE_KEYS:
        return (_SIMPLE_KEYS[n],)
    # Last resort: raw evdev name, e.g. "volumeup" -> KEY_VOLUMEUP.
    for candidate in (
        "KEY_" + n.upper().replace(" ", ""),
        "KEY_" + n.upper().replace(" ", "_"),
    ):
        code = _e.ecodes.get(candidate)
        if isinstance(code, int):
            return (code,)
    return None


def _name_for_code(code: int) -> str:
    if code in _MODIFIER_CODE_TO_NAME:
        return _MODIFIER_CODE_TO_NAME[code]
    if code in _CODE_TO_SIMPLE_NAME:
        return _CODE_TO_SIMPLE_NAME[code]
    raw = _e.KEY.get(code, f"KEY_{code}")
    if isinstance(raw, (list, tuple)):
        raw = raw[0]
    return str(raw).removeprefix("KEY_").lower()


def _parse_combo(combo: str) -> List[Tuple[int, ...]]:
    parts = [p for p in (combo or "").split("+") if p.strip()]
    if not parts:
        raise ValueError(f"Empty hotkey: {combo!r}")
    out = []
    for p in parts:
        codes = _codes_for_name(p)
        if codes is None:
            raise ValueError(f"Unknown key {p!r} in hotkey {combo!r}")
        out.append(codes)
    return out


def vk_for_name(name: str) -> Optional[int]:
    """Resolve a single key name (e.g. 'right alt') to an evdev key code."""
    codes = _codes_for_name(name)
    return codes[0] if codes else None


# --- Registration state ------------------------------------------------


class _Handler:
    def __init__(self, code, on_press, on_release):
        self.code = code
        self.on_press = on_press
        self.on_release = on_release
        self.is_down = False


class _Combo:
    def __init__(self, parts: List[Tuple[int, ...]], callback):
        self.parts = parts
        self.callback = callback

    def matches(self, pressed: Set[int]) -> bool:
        if len(pressed) != len(self.parts):
            return False
        return all(any(c in pressed for c in part) for part in self.parts)


class _Capture:
    def __init__(self):
        self.order: List[int] = []
        self.result: str = ""
        self.done = threading.Event()


_handlers: Dict[int, _Handler] = {}
_by_code: Dict[int, _Handler] = {}
_combos: Dict[int, _Combo] = {}
_captures: List[_Capture] = []
_pressed: Set[int] = set()
_state_lock = threading.Lock()
_token_source = itertools.count(1)


def _safe(fn: Callable[[], None]):
    try:
        fn()
    except Exception:
        pass


def _dispatch(cb: Optional[Callable[[], None]]):
    if cb is not None:
        threading.Thread(target=_safe, args=(cb,), daemon=True).start()


def _on_key(code: int, value: int):
    """Runs on the reader thread. value: 1 = down, 0 = up, 2 = autorepeat."""
    if value == 2:
        return
    with _state_lock:
        if value == 1:
            _pressed.add(code)
            for cap in _captures:
                if code not in cap.order:
                    cap.order.append(code)
            h = _by_code.get(code)
            if h is not None and not h.is_down:
                h.is_down = True
                debug_log.log("keyhook.press", code=code)
                _dispatch(h.on_press)
            for combo in _combos.values():
                if combo.matches(_pressed):
                    debug_log.log("keyhook.combo", code=code)
                    _dispatch(combo.callback)
        elif value == 0:
            for cap in list(_captures):
                held = [c for c in cap.order if c in _pressed]
                if held:
                    mods = [c for c in held if c in _MODIFIER_CODE_TO_NAME]
                    rest = [c for c in held if c not in _MODIFIER_CODE_TO_NAME]
                    cap.result = "+".join(_name_for_code(c) for c in mods + rest)
                    cap.done.set()
                    _captures.remove(cap)
            _pressed.discard(code)
            h = _by_code.get(code)
            if h is not None and h.is_down:
                h.is_down = False
                debug_log.log("keyhook.release", code=code)
                _dispatch(h.on_release)


# --- Reader thread -----------------------------------------------------

_thread: Optional[threading.Thread] = None
_ready = threading.Event()
_stopping = threading.Event()
_device_count = 0


def _is_keyboard(dev: evdev.InputDevice) -> bool:
    keys = dev.capabilities().get(_e.EV_KEY, [])
    return _e.KEY_SPACE in keys or _e.KEY_LEFTCTRL in keys or _e.KEY_RIGHTALT in keys


def _scan(devices: Dict[str, evdev.InputDevice], ignored: Set[str]):
    """Open any newly-attached keyboards. `ignored` caches non-keyboard paths
    so we don't reopen every mouse on every rescan."""
    global _device_count
    present = set(evdev.list_devices())  # only lists devices we can read
    ignored &= present
    for path in present:
        if path in devices or path in ignored:
            continue
        try:
            dev = evdev.InputDevice(path)
        except OSError:
            ignored.add(path)
            continue
        if dev.name == VIRTUAL_DEVICE_NAME or not _is_keyboard(dev):
            dev.close()
            ignored.add(path)
            continue
        devices[path] = dev
        debug_log.log("keyhook.device_added", path=path, name=dev.name)
    _device_count = len(devices)


def _drop(devices: Dict[str, evdev.InputDevice], path: str):
    global _device_count
    dev = devices.pop(path, None)
    if dev is not None:
        try:
            dev.close()
        except Exception:
            pass
        debug_log.log("keyhook.device_removed", path=path)
    _device_count = len(devices)


def _thread_main():
    devices: Dict[str, evdev.InputDevice] = {}
    ignored: Set[str] = set()
    _scan(devices, ignored)
    last_scan = time.monotonic()
    debug_log.log("keyhook.installed", devices=len(devices))
    _ready.set()

    while not _stopping.is_set():
        if time.monotonic() - last_scan > _RESCAN_INTERVAL:
            _scan(devices, ignored)
            last_scan = time.monotonic()
        if not devices:
            _stopping.wait(_RESCAN_INTERVAL)
            continue

        by_fd = {dev.fd: path for path, dev in devices.items()}
        try:
            readable, _, _ = select.select(list(by_fd), [], [], 0.5)
        except (OSError, ValueError):
            # A device vanished between scan and select; rescan next loop.
            last_scan = 0.0
            continue

        for fd in readable:
            path = by_fd[fd]
            dev = devices.get(path)
            if dev is None:
                continue
            try:
                for ev in dev.read():
                    if ev.type == _e.EV_KEY:
                        _on_key(ev.code, ev.value)
            except BlockingIOError:
                pass
            except OSError:
                _drop(devices, path)

    for path in list(devices):
        _drop(devices, path)
    debug_log.log("keyhook.stopped")


# --- Public API --------------------------------------------------------


def start() -> bool:
    """Start the reader thread. Idempotent. Returns True if at least one
    keyboard device could be opened."""
    global _thread
    with _state_lock:
        if _thread is not None and _thread.is_alive():
            return is_installed()
        _ready.clear()
        _stopping.clear()
        _thread = threading.Thread(target=_thread_main, name="keyhook", daemon=True)
        _thread.start()
    _ready.wait(timeout=2.0)
    return is_installed()


def stop() -> None:
    global _thread
    t = _thread
    if t is None or not t.is_alive():
        _thread = None
        return
    _stopping.set()
    t.join(timeout=2.0)
    _thread = None


def is_installed() -> bool:
    return _thread is not None and _thread.is_alive() and _device_count > 0


def register_key(
    code: int,
    on_press: Optional[Callable[[], None]],
    on_release: Optional[Callable[[], None]] = None,
) -> int:
    token = next(_token_source)
    h = _Handler(code, on_press, on_release)
    with _state_lock:
        for tk, existing in list(_handlers.items()):
            if existing.code == code:
                _handlers.pop(tk, None)
        _handlers[token] = h
        _by_code[code] = h
    debug_log.log("keyhook.register_key", code=code, token=token)
    return token


def unregister(token: int) -> None:
    with _state_lock:
        h = _handlers.pop(token, None)
        if h is not None and _by_code.get(h.code) is h:
            _by_code.pop(h.code, None)


def add_hotkey(combo: str, callback: Callable[[], None]) -> int:
    """Fire `callback` when exactly the keys in `combo` (e.g.
    'ctrl+shift+space') are held. Raises ValueError for unknown keys."""
    parts = _parse_combo(combo)
    start()
    token = next(_token_source)
    with _state_lock:
        _combos[token] = _Combo(parts, callback)
    debug_log.log("keyhook.add_hotkey", combo=combo, token=token)
    return token


def remove_hotkey(token: int) -> None:
    with _state_lock:
        _combos.pop(token, None)


def read_hotkey() -> str:
    """Block until the user presses and releases a key combo; return it as
    a string like 'ctrl+shift+space' or 'right alt'."""
    if not start():
        return ""
    cap = _Capture()
    with _state_lock:
        _captures.append(cap)
    cap.done.wait()
    return cap.result
