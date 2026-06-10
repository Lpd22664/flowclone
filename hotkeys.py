"""Global hotkey listener.

PTT (press-and-hold) goes through `keyhook` — our raw WH_KEYBOARD_LL hook.
The `keyboard` library has a stateful AltGr compensation filter that can
silently swallow Right Alt events in certain foreground contexts (notably
when a console window has focus); the raw hook has no such quirk.

Combo hotkeys (Command Mode, Settings) go through `keyboard.add_hotkey`.
They aren't press-and-hold, they don't touch the Right Alt path, and
add_hotkey uses a stable dispatch mechanism that's been reliable in
practice.

Both mechanisms run on background threads; handlers dispatch onto worker
threads so nothing heavy runs inside the LL-hook callback (Windows silently
disables LL hooks whose callback exceeds LowLevelHooksTimeout, default
300 ms, and sounddevice device enumeration can come close to that).
"""
from __future__ import annotations

import glob
import os
import select
import struct
import sys
import threading
from typing import Callable, Optional

import keyboard

import debug_log

if sys.platform == "win32":
    import keyhook
else:
    keyhook = None


class HotkeyManager:
    """
    Push-to-talk hotkey: fires on_ptt_press when held, on_ptt_release when released.
    Command mode hotkey: fires on_command_toggle on each press.
    Settings hotkey: fires on_settings on each press.
    """

    def __init__(
        self,
        on_ptt_press: Callable[[], None],
        on_ptt_release: Callable[[], None],
        on_command_toggle: Callable[[], None],
        on_settings: Callable[[], None],
    ):
        self._on_ptt_press = on_ptt_press
        self._on_ptt_release = on_ptt_release
        self._on_command_toggle = on_command_toggle
        self._on_settings = on_settings

        self._ptt_hotkey: Optional[str] = None
        self._command_hotkey: Optional[str] = None
        self._settings_hotkey: Optional[str] = None

        self._ptt_token: Optional[int] = None
        self._ptt_press_id = None
        self._ptt_release_id = None
        self._command_hotkey_id = None
        self._settings_hotkey_id = None
        self.last_error: str | None = None

        self._lock = threading.Lock()

    # --- Registration ---------------------------------------------------

    def _clear(self):
        if self._ptt_token is not None:
            try:
                if keyhook is not None:
                    keyhook.unregister(self._ptt_token)
            except Exception:
                pass
            self._ptt_token = None

        for attr in ("_ptt_press_id", "_ptt_release_id"):
            hid = getattr(self, attr)
            if hid is not None:
                if attr == "_ptt_press_id" and isinstance(hid, tuple) and len(hid) == 4:
                    d, root, grabs, stop = hid
                    try:
                        stop.set()
                        for keycode, mask in grabs:
                            try:
                                root.ungrab_key(keycode, mask)
                            except Exception:
                                pass
                        d.close()
                    except Exception:
                        pass
                elif attr == "_ptt_press_id" and isinstance(hid, tuple) and len(hid) == 3 and hid[0] == "evdev":
                    _kind, devices, stop = hid
                    try:
                        stop.set()
                        for dev in devices:
                            try:
                                dev.close()
                            except Exception:
                                pass
                    except Exception:
                        pass
                elif attr == "_ptt_release_id" and isinstance(hid, threading.Thread):
                    pass
                else:
                    try:
                        keyboard.unhook_key(hid)
                    except Exception:
                        try:
                            keyboard.unhook(hid)
                        except Exception:
                            pass
            setattr(self, attr, None)

        for attr in ("_command_hotkey_id", "_settings_hotkey_id"):
            hid = getattr(self, attr)
            if hid is not None:
                try:
                    keyboard.remove_hotkey(hid)
                except Exception:
                    pass
            setattr(self, attr, None)

    def apply(self, ptt: str, command: str, settings: str):
        """(Re)register all hotkeys. Safe to call multiple times."""
        with self._lock:
            self._clear()
            self.last_error = None
            self._ptt_hotkey = (ptt or "").strip().lower()
            self._command_hotkey = command
            self._settings_hotkey = settings

            if sys.platform == "win32":
                self._register_windows_ptt()
            else:
                self._register_keyboard_ptt()

            if sys.platform == "win32" or not self._linux_native_registered():
                try:
                    self._command_hotkey_id = keyboard.add_hotkey(
                        command, self._safe(self._on_command_toggle),
                    )
                except Exception as e:
                    self._command_hotkey_id = None
                    self._record_error(f"command hotkey: {e}")

                try:
                    self._settings_hotkey_id = keyboard.add_hotkey(
                        settings, self._safe(self._on_settings),
                    )
                except Exception as e:
                    self._settings_hotkey_id = None
                    self._record_error(f"settings hotkey: {e}")

    def _x11_registered(self) -> bool:
        return isinstance(self._ptt_press_id, tuple) and len(self._ptt_press_id) == 4

    def _evdev_registered(self) -> bool:
        return (
            isinstance(self._ptt_press_id, tuple)
            and len(self._ptt_press_id) == 3
            and self._ptt_press_id[0] == "evdev"
        )

    def _linux_native_registered(self) -> bool:
        return self._x11_registered() or self._evdev_registered()

    def _record_error(self, msg: str):
        if self.last_error is None:
            self.last_error = msg
        debug_log.log("hotkeys.error", error=msg)

    def _register_windows_ptt(self):
        if keyhook is None:
            return
        vk = keyhook.vk_for_name(self._ptt_hotkey)
        if vk is None:
            debug_log.log("hotkeys.ptt_unmapped", name=self._ptt_hotkey)
            return
        keyhook.start()
        self._ptt_token = keyhook.register_key(
            vk,
            on_press=self._safe(self._on_ptt_press),
            on_release=self._safe(self._on_ptt_release),
        )
        debug_log.log(
            "hotkeys.ptt_registered",
            name=self._ptt_hotkey, vk=hex(vk), token=self._ptt_token,
        )

    def _register_keyboard_ptt(self):
        if self._register_evdev_hotkeys():
            return
        if self._register_x11_hotkeys():
            return
        self._register_keyboard_linux_hotkeys()

    def _register_evdev_hotkeys(self) -> bool:
        """Linux user-mode backend using readable /dev/input/event* devices.

        The `keyboard` package refuses to run unless the process is root, even
        when the current user can read specific event devices through groups
        such as plugdev. Reading input_event structs directly gives FlowClone a
        Wayland-compatible path for Right Alt without running the GUI as root.
        """
        if sys.platform != "linux":
            return False

        EV_KEY = 0x01
        KEY_CODES = {
            "left ctrl": 29,
            "ctrl": 29,
            "control": 29,
            "left shift": 42,
            "shift": 42,
            "left alt": 56,
            "alt": 56,
            "space": 57,
            "right ctrl": 97,
            "right alt": 100,
            "alt gr": 100,
            "f": 33,
        }

        def codes_for(name: str) -> set[int]:
            key = (name or "").strip().lower()
            if key == "shift":
                return {42, 54}
            if key in ("ctrl", "control"):
                return {29, 97}
            return {KEY_CODES[key]} if key in KEY_CODES else set()

        def parse_combo(combo: str) -> tuple[set[int], int] | None:
            parts = [p.strip().lower() for p in (combo or "").split("+") if p.strip()]
            if not parts:
                return None
            trigger_codes = codes_for(parts[-1])
            if not trigger_codes:
                return None
            required: set[int] = set()
            for part in parts[:-1]:
                required.update(codes_for(part))
            return required, next(iter(trigger_codes))

        ptt_codes = codes_for(self._ptt_hotkey)
        command = parse_combo(self._command_hotkey or "")
        settings = parse_combo(self._settings_hotkey or "")
        if not ptt_codes and command is None and settings is None:
            return False

        devices: list[object] = []
        for path in sorted(glob.glob("/dev/input/event*")):
            if not os.access(path, os.R_OK):
                continue
            try:
                fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
            except OSError:
                continue
            devices.append(os.fdopen(fd, "rb", buffering=0))

        if not devices:
            self._record_error("evdev hotkeys: no readable /dev/input/event* devices")
            return False

        stop = threading.Event()
        keys_down: set[int] = set()
        ptt_down = False
        event_struct = struct.Struct("llHHI")

        def combo_active(parsed: tuple[set[int], int] | None, code: int) -> bool:
            if parsed is None:
                return False
            required, trigger = parsed
            return code == trigger and required.issubset(keys_down)

        def loop():
            nonlocal ptt_down
            while not stop.is_set():
                try:
                    readable, _, _ = select.select(devices, [], [], 0.25)
                except Exception:
                    break
                for dev in readable:
                    while not stop.is_set():
                        try:
                            data = dev.read(event_struct.size)
                        except BlockingIOError:
                            break
                        except OSError:
                            break
                        if not data or len(data) < event_struct.size:
                            break
                        _sec, _usec, ev_type, code, value = event_struct.unpack(data)
                        if ev_type != EV_KEY:
                            continue

                        if value in (1, 2):
                            keys_down.add(code)
                        elif value == 0:
                            keys_down.discard(code)

                        if code in ptt_codes:
                            if value == 1 and not ptt_down:
                                ptt_down = True
                                threading.Thread(
                                    target=self._safe(self._on_ptt_press), daemon=True
                                ).start()
                            elif value == 0 and ptt_down:
                                ptt_down = False
                                threading.Thread(
                                    target=self._safe(self._on_ptt_release), daemon=True
                                ).start()
                            continue

                        if value == 1 and combo_active(command, code):
                            threading.Thread(
                                target=self._safe(self._on_command_toggle), daemon=True
                            ).start()
                        elif value == 1 and combo_active(settings, code):
                            threading.Thread(
                                target=self._safe(self._on_settings), daemon=True
                            ).start()

            for dev in devices:
                try:
                    dev.close()
                except Exception:
                    pass

        thread = threading.Thread(target=loop, name="evdev-hotkeys", daemon=True)
        thread.start()
        self._ptt_press_id = ("evdev", devices, stop)
        self._ptt_release_id = thread
        debug_log.log(
            "hotkeys.evdev_registered",
            devices=len(devices),
            ptt_codes=sorted(ptt_codes),
            command=bool(command),
            settings=bool(settings),
        )
        return True

    def _register_x11_hotkeys(self) -> bool:
        """Linux user-mode backend using XGrabKey.

        This works on Xorg and may work for XWayland clients. Native Wayland
        compositors can deny global key grabs, in which case we fall back to
        the `keyboard` package's root-only evdev backend.
        """
        try:
            from Xlib import X, XK, display
        except Exception as e:
            self._record_error(f"x11 hotkeys unavailable: {e}")
            return False

        try:
            d = display.Display()
            root = d.screen().root
        except Exception as e:
            self._record_error(f"x11 display: {e}")
            return False

        special_keysyms = {
            # python-xlib 0.33 does not resolve this name via string_to_keysym
            # on some systems, but xmodmap exposes it as keysym 0xFE03.
            "ISO_Level3_Shift": 0xFE03,
        }

        def keysym_for(symbol: str) -> int:
            return special_keysyms.get(symbol, XK.string_to_keysym(symbol))

        def keycodes_from_xmodmap(symbols: list[str]) -> list[int]:
            """Resolve symbols from the current X keymap.

            python-xlib's keysym_to_keycode returns 0 for ISO_Level3_Shift on
            some GB/AltGr layouts even though xmodmap exposes the real keycodes.
            Right Alt is often a modifier-only key, so this fallback is the
            difference between "right alt" being registered and silently missed.
            """
            try:
                mapping = d.get_keyboard_mapping(
                    d.display.info.min_keycode,
                    d.display.info.max_keycode - d.display.info.min_keycode + 1,
                )
            except Exception:
                return []

            wanted = {
                keysym_for(symbol)
                for symbol in symbols
                if keysym_for(symbol)
            }
            if not wanted:
                return []

            out: list[int] = []
            first_keycode = d.display.info.min_keycode
            for offset, keysyms in enumerate(mapping):
                if any(keysym in wanted for keysym in keysyms):
                    out.append(first_keycode + offset)
            return out

        def keycodes_for(name: str) -> list[int]:
            keysym_names = {
                "right alt": ["ISO_Level3_Shift", "Alt_R"],
                "alt gr": ["ISO_Level3_Shift", "Alt_R"],
                "left alt": ["Alt_L"],
                "alt": ["Alt_L"],
                "space": ["space"],
                "f": ["f", "F"],
            }.get(name, [name])
            keycodes: list[int] = []
            for keysym_name in keysym_names:
                keysym = keysym_for(keysym_name)
                if keysym:
                    keycode = d.keysym_to_keycode(keysym)
                    if keycode:
                        keycodes.append(keycode)
            keycodes.extend(keycodes_from_xmodmap(keysym_names))
            return list(dict.fromkeys(keycodes))

        def keycode_for(name: str) -> int | None:
            codes = keycodes_for(name)
            return codes[0] if codes else None

        modifier_map = {
            "ctrl": X.ControlMask,
            "control": X.ControlMask,
            "shift": X.ShiftMask,
            "alt": X.Mod1Mask,
            "left alt": X.Mod1Mask,
            "right alt": X.Mod5Mask,
            "alt gr": X.Mod5Mask,
        }

        def parse_combo(combo: str) -> tuple[int, int] | None:
            parts = [p.strip().lower() for p in combo.split("+") if p.strip()]
            if not parts:
                return None
            key_name = parts[-1]
            keycode = keycode_for(key_name)
            if not keycode:
                return None
            mask = 0
            for mod in parts[:-1]:
                mask |= modifier_map.get(mod, 0)
            return keycode, mask

        grabs: dict[tuple[int, int], Callable[[], None]] = {}
        release_grabs: dict[int, Callable[[], None]] = {}

        ptt_keycodes = keycodes_for(self._ptt_hotkey)
        for ptt_keycode in ptt_keycodes:
            grabs[(ptt_keycode, 0)] = self._safe(self._on_ptt_press)
            release_grabs[ptt_keycode] = self._safe(self._on_ptt_release)

        command = parse_combo(self._command_hotkey or "")
        if command:
            grabs[command] = self._safe(self._on_command_toggle)
        settings = parse_combo(self._settings_hotkey or "")
        if settings:
            grabs[settings] = self._safe(self._on_settings)

        if not grabs:
            self._record_error("x11 hotkeys: no supported key mappings")
            return False

        ignored_locks = [0, X.LockMask, X.Mod2Mask, X.LockMask | X.Mod2Mask]
        try:
            for (keycode, mask), _callback in grabs.items():
                for lock_mask in ignored_locks:
                    root.grab_key(
                        keycode,
                        mask | lock_mask,
                        True,
                        X.GrabModeAsync,
                        X.GrabModeAsync,
                    )
            d.flush()
        except Exception as e:
            self._record_error(f"x11 grab: {e}")
            try:
                d.close()
            except Exception:
                pass
            return False

        stop = threading.Event()

        def loop():
            while not stop.is_set():
                try:
                    event = d.next_event()
                except Exception:
                    break
                key = getattr(event, "detail", None)
                state = getattr(event, "state", 0) & ~(X.LockMask | X.Mod2Mask)
                if event.type == X.KeyPress:
                    callback = grabs.get((key, state)) or grabs.get((key, 0))
                    if callback is not None:
                        threading.Thread(target=callback, daemon=True).start()
                elif event.type == X.KeyRelease:
                    callback = release_grabs.get(key)
                    if callback is not None:
                        threading.Thread(target=callback, daemon=True).start()

        thread = threading.Thread(target=loop, name="x11-hotkeys", daemon=True)
        thread.start()
        self._ptt_press_id = (d, root, grabs, stop)
        self._ptt_release_id = thread
        debug_log.log("hotkeys.x11_registered", grabs=len(grabs), ptt_keycodes=ptt_keycodes)
        return True

    def _register_keyboard_linux_hotkeys(self):
        """Linux fallback using keyboard's evdev hook.

        `keyboard` usually needs permission to read /dev/input on Linux. If
        registration fails, command/settings hotkeys may fail too, but the
        app should keep running so Settings can still be opened from the tray.
        """
        if not self._ptt_hotkey:
            return
        try:
            self._ptt_press_id = keyboard.on_press_key(
                self._ptt_hotkey,
                lambda _event: self._safe(self._on_ptt_press)(),
                suppress=False,
            )
            self._ptt_release_id = keyboard.on_release_key(
                self._ptt_hotkey,
                lambda _event: self._safe(self._on_ptt_release)(),
                suppress=False,
            )
            debug_log.log("hotkeys.ptt_registered_linux", name=self._ptt_hotkey)
        except Exception as e:
            self._ptt_press_id = None
            self._ptt_release_id = None
            self._record_error(f"push-to-talk hotkey: {e}")
            debug_log.log("hotkeys.ptt_linux_failed", name=self._ptt_hotkey, error=str(e))

    def any_registered(self) -> bool:
        return any(
            h is not None
            for h in (
                self._ptt_token,
                self._ptt_press_id,
                self._ptt_release_id,
                self._command_hotkey_id,
                self._settings_hotkey_id,
            )
        )

    @staticmethod
    def _safe(fn: Callable[[], None]) -> Callable[[], None]:
        def wrapper():
            try:
                fn()
            except Exception:
                pass
        return wrapper

    def shutdown(self):
        with self._lock:
            self._clear()
        try:
            if keyhook is not None:
                keyhook.stop()
        except Exception:
            pass

    # --- Utility --------------------------------------------------------

    @staticmethod
    def capture_hotkey(timeout: float | None = None) -> str:
        """Blocking: wait for the next hotkey combo and return its string representation.

        Uses `keyboard.read_hotkey` — it's a one-off blocking call inside the
        settings dialog, not sensitive to the cmd/terminal issue that affected
        push-to-talk detection.
        """
        return keyboard.read_hotkey(suppress=False)
