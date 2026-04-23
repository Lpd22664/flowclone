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

import threading
from typing import Callable, Optional

import keyboard

import debug_log
import keyhook


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
        self._command_hotkey_id = None
        self._settings_hotkey_id = None

        self._lock = threading.Lock()

    # --- Registration ---------------------------------------------------

    def _clear(self):
        if self._ptt_token is not None:
            try:
                keyhook.unregister(self._ptt_token)
            except Exception:
                pass
            self._ptt_token = None

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
            self._ptt_hotkey = (ptt or "").strip().lower()
            self._command_hotkey = command
            self._settings_hotkey = settings

            vk = keyhook.vk_for_name(self._ptt_hotkey)
            if vk is None:
                # Unknown single-key name (e.g. a combo typed into the field
                # we don't support for PTT yet). Fall back silently; we won't
                # register a press-and-hold.
                debug_log.log("hotkeys.ptt_unmapped", name=self._ptt_hotkey)
            else:
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

            try:
                self._command_hotkey_id = keyboard.add_hotkey(
                    command, self._safe(self._on_command_toggle),
                )
            except Exception:
                self._command_hotkey_id = None

            try:
                self._settings_hotkey_id = keyboard.add_hotkey(
                    settings, self._safe(self._on_settings),
                )
            except Exception:
                self._settings_hotkey_id = None

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
