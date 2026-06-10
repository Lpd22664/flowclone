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

            if sys.platform == "win32" or not self._x11_registered():
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
        if self._register_x11_hotkeys():
            return
        self._register_keyboard_linux_hotkeys()

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

        def keycode_for(name: str) -> int | None:
            keysym_names = {
                "right alt": ["ISO_Level3_Shift", "Alt_R"],
                "alt gr": ["ISO_Level3_Shift", "Alt_R"],
                "left alt": ["Alt_L"],
                "alt": ["Alt_L"],
                "space": ["space"],
                "f": ["f", "F"],
            }.get(name, [name])
            for keysym_name in keysym_names:
                keysym = XK.string_to_keysym(keysym_name)
                if keysym:
                    keycode = d.keysym_to_keycode(keysym)
                    if keycode:
                        return keycode
            return None

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

        ptt_keycode = keycode_for(self._ptt_hotkey)
        if ptt_keycode:
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
        debug_log.log("hotkeys.x11_registered", grabs=len(grabs))
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
