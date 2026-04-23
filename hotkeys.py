"""Global hotkey listener. Runs on a daemon thread managed by the `keyboard` lib.

Important Windows constraint: the WH_KEYBOARD_LL callback must return within
~300 ms (LowLevelHooksTimeout default) or Windows silently disables the hook
for the rest of the session. Starting a sounddevice stream can take 100–200 ms
on USB/Bluetooth microphones, so we dispatch press/release handlers onto a
worker thread and return immediately from the hook.
"""
from __future__ import annotations

import threading
from typing import Callable, Optional

import keyboard

import debug_log


class HotkeyManager:
    """
    Push-to-talk hotkey: fires on_ptt_press when held, on_ptt_release when released.
    Command mode hotkey: fires on_command_toggle on each press (toggle behaviour handled by caller).
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

        self._ptt_down = False
        self._ptt_handler_ref = None  # for keyboard.hook
        self._command_hotkey_id = None
        self._settings_hotkey_id = None

        self._lock = threading.Lock()

    # --- Helpers --------------------------------------------------------

    @staticmethod
    def _normalise_ptt(hotkey: str) -> str:
        return hotkey.lower().strip()

    def _ptt_key_matches(self, event: keyboard.KeyboardEvent) -> bool:
        if self._ptt_hotkey is None:
            return False
        target = self._ptt_hotkey
        name = (event.name or "").lower()
        if name == target:
            return True
        # alt aliases
        if target == "right alt" and name in ("alt gr", "right alt"):
            return True
        if target == "left alt" and name == "alt":
            # 'alt' is ambiguous; prefer scan code check when possible
            return True
        return False

    def _on_ptt_event(self, event: keyboard.KeyboardEvent):
        debug_log.log(
            "hook.event",
            type=event.event_type,
            name=event.name,
            scan=getattr(event, "scan_code", None),
        )
        if not self._ptt_key_matches(event):
            return
        if event.event_type == keyboard.KEY_DOWN:
            if not self._ptt_down:
                self._ptt_down = True
                debug_log.log("hook.ptt_down", name=event.name)
                # Dispatch off the hook thread. See module docstring — LL hook
                # callbacks that exceed LowLevelHooksTimeout get silently
                # disabled by Windows.
                threading.Thread(
                    target=self._safe(self._on_ptt_press),
                    daemon=True, name="ptt-press",
                ).start()
        elif event.event_type == keyboard.KEY_UP:
            if self._ptt_down:
                self._ptt_down = False
                debug_log.log("hook.ptt_up", name=event.name)
                threading.Thread(
                    target=self._safe(self._on_ptt_release),
                    daemon=True, name="ptt-release",
                ).start()

    # --- Registration ---------------------------------------------------

    def _clear(self):
        # Remove prior bindings if any.
        try:
            if self._ptt_handler_ref is not None:
                keyboard.unhook(self._ptt_handler_ref)
        except Exception:
            pass
        self._ptt_handler_ref = None

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
            self._ptt_hotkey = self._normalise_ptt(ptt)
            self._command_hotkey = command
            self._settings_hotkey = settings
            self._ptt_down = False

            self._ptt_handler_ref = keyboard.hook(self._on_ptt_event)

            try:
                self._command_hotkey_id = keyboard.add_hotkey(
                    command, self._safe(self._on_command_toggle)
                )
            except Exception:
                self._command_hotkey_id = None

            try:
                self._settings_hotkey_id = keyboard.add_hotkey(
                    settings, self._safe(self._on_settings)
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

    # --- Utility --------------------------------------------------------

    @staticmethod
    def capture_hotkey(timeout: float | None = None) -> str:
        """Blocking: wait for the next hotkey combo and return its string representation."""
        return keyboard.read_hotkey(suppress=False)
