"""FlowClone entry point."""
from __future__ import annotations

import ctypes
import sys
import threading
import traceback

from PyQt6.QtCore import QObject, Qt, pyqtSignal, pyqtSlot
from PyQt6.QtWidgets import QApplication

from config import config
from overlay import Overlay
from tray import Tray
from hotkeys import HotkeyManager
from audio import Recorder, MicNotFoundError
from transcription import transcribe, TranscriptionError
from ai_processor import cleanup, AIError
from injector import inject_text, ElevationRequired
from command_mode import CommandModeController
from settings_window import SettingsDialog


def _is_admin() -> bool:
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


class App(QObject):
    """Main controller. Lives on the Qt main thread."""

    open_settings_signal = pyqtSignal()

    def __init__(self, qt_app: QApplication):
        super().__init__()
        self._qt_app = qt_app

        self.overlay = Overlay()
        self.recorder = Recorder(on_level=self._emit_level)
        self._ptt_thread: threading.Thread | None = None
        self._ptt_active = False
        self._ptt_lock = threading.Lock()

        self.command = CommandModeController(
            overlay=self.overlay, on_error=self._show_error
        )

        self.hotkeys = HotkeyManager(
            on_ptt_press=self._on_ptt_press,
            on_ptt_release=self._on_ptt_release,
            on_command_toggle=self.command.toggle,
            on_settings=self._request_open_settings,
        )

        self.tray = Tray(
            on_open_settings=self._request_open_settings,
            on_toggle_ai_cleanup=lambda _new: None,
            on_quit=self._quit,
        )

        self._settings_dialog: SettingsDialog | None = None
        self.open_settings_signal.connect(self._open_settings)

    # --- Lifecycle ------------------------------------------------------

    def start(self):
        self.tray.start()
        self._apply_hotkeys()

        if not config.api_key:
            self.overlay.show_error_signal.emit("No API key — open Settings")

    def _apply_hotkeys(self):
        self.hotkeys.apply(
            ptt=config.get("push_to_talk_hotkey", "right alt"),
            command=config.get("command_mode_hotkey", "ctrl+shift+space"),
            settings=config.get("settings_hotkey", "ctrl+shift+f"),
        )

    def _quit(self):
        try:
            self.hotkeys.shutdown()
        except Exception:
            pass
        try:
            self.tray.stop()
        except Exception:
            pass
        self._qt_app.quit()

    # --- Push-to-talk pipeline ------------------------------------------

    def _on_ptt_press(self):
        """Keyboard thread. Start recording, flash overlay."""
        with self._ptt_lock:
            if self._ptt_active:
                return
            if not config.api_key:
                self.overlay.show_error_signal.emit("No API key — open Settings")
                return
            try:
                self.recorder.start()
            except MicNotFoundError:
                self.overlay.show_error_signal.emit("No microphone detected")
                return
            except Exception as e:
                self.overlay.show_error_signal.emit(str(e))
                return
            self._ptt_active = True
        self.overlay.show_recording_signal.emit()

    def _on_ptt_release(self):
        """Keyboard thread. Stop recording, spawn worker to transcribe/process/inject."""
        with self._ptt_lock:
            if not self._ptt_active:
                return
            self._ptt_active = False

        try:
            wav_bytes, duration = self.recorder.stop()
        except Exception as e:
            self.overlay.show_error_signal.emit(str(e))
            return

        min_dur = float(config.get("min_recording_seconds", 0.5))
        if duration < min_dur or not wav_bytes:
            self.overlay.hide_signal.emit()
            return

        self.overlay.show_processing_signal.emit()
        worker = threading.Thread(
            target=self._process_recording, args=(wav_bytes,), daemon=True
        )
        worker.start()

    def _process_recording(self, wav_bytes: bytes):
        try:
            raw = transcribe(wav_bytes)
        except TranscriptionError as e:
            self.overlay.show_error_signal.emit(f"Transcription failed: {e}")
            return
        except Exception as e:
            self.overlay.show_error_signal.emit(str(e))
            return

        if not raw.strip():
            self.overlay.hide_signal.emit()
            return

        try:
            cleaned = cleanup(raw)
        except AIError as e:
            self.overlay.show_error_signal.emit(f"AI failed: {e}")
            return

        if not cleaned.strip():
            self.overlay.hide_signal.emit()
            return

        try:
            inject_text(cleaned)
        except ElevationRequired:
            self.overlay.show_error_signal.emit(
                "Target window is elevated — right-click tray → Run as admin"
            )
            return
        except Exception as e:
            self.overlay.show_error_signal.emit(f"Inject failed: {e}")
            return

        self.overlay.show_done_signal.emit()

    # --- Settings -------------------------------------------------------

    def _request_open_settings(self):
        # Can be called from any thread. Route to main thread via signal.
        self.open_settings_signal.emit()

    @pyqtSlot()
    def _open_settings(self):
        if self._settings_dialog is not None and self._settings_dialog.isVisible():
            self._settings_dialog.raise_()
            self._settings_dialog.activateWindow()
            return
        self._settings_dialog = SettingsDialog()
        self._settings_dialog.saved.connect(self._on_settings_saved)
        self._settings_dialog.show()
        self._settings_dialog.raise_()
        self._settings_dialog.activateWindow()

    @pyqtSlot()
    def _on_settings_saved(self):
        self._apply_hotkeys()
        self.tray.refresh_menu()

    # --- Errors ---------------------------------------------------------

    def _show_error(self, msg: str):
        self.overlay.show_error_signal.emit(msg)

    # --- Audio level bridge --------------------------------------------

    def _emit_level(self, rms: float):
        # Called from the sounddevice audio thread. Qt signals are thread-safe
        # across threads as long as receivers live on the main thread.
        self.overlay.amplitude_signal.emit(rms)


def _excepthook(exc_type, exc, tb):
    traceback.print_exception(exc_type, exc, tb)


def main():
    sys.excepthook = _excepthook

    qt_app = QApplication(sys.argv)
    qt_app.setQuitOnLastWindowClosed(False)
    qt_app.setAttribute(Qt.ApplicationAttribute.AA_DontShowIconsInMenus, False)

    controller = App(qt_app)
    controller.start()

    if not _is_admin():
        # Non-fatal: surface via overlay after a moment so users know.
        # Many apps still work without admin; games/UAC-elevated apps won't.
        pass

    rc = qt_app.exec()
    try:
        controller._quit()
    except Exception:
        pass
    sys.exit(rc)


if __name__ == "__main__":
    main()
