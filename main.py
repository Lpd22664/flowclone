"""FlowClone entry point."""

from __future__ import annotations

import ctypes
import os
import signal
import sys
import threading
import traceback

from PyQt6.QtCore import QObject, Qt, QTimer, pyqtSignal, pyqtSlot
from PyQt6.QtGui import QGuiApplication
from PyQt6.QtWidgets import QApplication

from config import config, PROVIDER_LOCAL_LLAMA
from overlay import Overlay
from tray import Tray
from hotkeys import HotkeyManager
from audio import Recorder, MicNotFoundError
from transcription import transcribe, TranscriptionError
from ai_processor import cleanup, AIError
from command_mode import CommandModeController
from settings_window import SettingsDialog
import llamacpp
import selection_transform
import transcript_log
import whispercpp

if sys.platform == "win32":
    from injector import inject_text, ElevationRequired
else:
    import keyhook_linux
    from injector_linux import inject_text, ElevationRequired


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
        self.recorder = Recorder(
            on_level=self._emit_level, device=config.get("input_device") or None
        )
        self._ptt_thread: threading.Thread | None = None
        self._ptt_active = False
        # Which hold-to-record hotkey owns the recorder: "dictation" or
        # "transform" (Selection Transform).
        self._ptt_mode: str | None = None
        self._ptt_lock = threading.Lock()

        self.command = CommandModeController(
            overlay=self.overlay, on_error=self._show_error
        )

        self.hotkeys = HotkeyManager(
            on_ptt_press=self._on_ptt_press,
            on_ptt_release=self._on_ptt_release,
            on_command_toggle=self.command.toggle,
            on_settings=self._request_open_settings,
            on_transform_press=self._on_transform_press,
            on_transform_release=self._on_transform_release,
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
        # "Follow system" colour scheme: swap the tray glyph when the desktop
        # switches between light and dark.
        QGuiApplication.styleHints().colorSchemeChanged.connect(
            lambda _scheme: self.tray.refresh_icon()
        )
        self._apply_hotkeys()
        self._apply_whisper_server()
        self._apply_llama_server()

        if sys.platform != "win32" and not keyhook_linux.is_installed():
            print(
                "FlowClone: no readable keyboard devices in /dev/input — global "
                "hotkeys are disabled. Add your user to the 'input' group "
                "(sudo usermod -aG input $USER) and log in again.",
                file=sys.stderr,
            )
            self.overlay.show_error_signal.emit("Can't read /dev/input")
            return

        if config.missing_api_key:
            self.overlay.show_error_signal.emit("No API key — open Settings")

    def _apply_whisper_server(self):
        """Run whisper-server with the selected model iff the local provider is active."""
        if not config.is_local:
            whispercpp.server.stop()
            return
        model = config.get("whisper_model", "base")
        if not whispercpp.is_downloaded(model):
            whispercpp.server.stop()
            self.overlay.show_error_signal.emit(f"Model {model} not downloaded")
            return
        try:
            whispercpp.server.start(model)  # restarts if the model changed
        except whispercpp.WhisperServerError as e:
            self.overlay.show_error_signal.emit(str(e))

    def _apply_llama_server(self):
        """Run llama-server with the selected model iff the local llama.cpp AI provider is active."""
        if config.ai_provider != PROVIDER_LOCAL_LLAMA:
            llamacpp.server.stop()
            return
        model = config.get("llama_model", "")
        if model not in llamacpp.MODELS or not llamacpp.is_downloaded(model):
            llamacpp.server.stop()
            self.overlay.show_error_signal.emit(f"Model {model} not downloaded")
            return
        try:
            llamacpp.server.start(model)  # restarts if the model changed
        except llamacpp.LlamaServerError as e:
            self.overlay.show_error_signal.emit(str(e))

    def _apply_hotkeys(self):
        self.hotkeys.apply(
            ptt=config.get("push_to_talk_hotkey", "right alt"),
            command=config.get("command_mode_hotkey", "ctrl+shift+space"),
            settings=config.get("settings_hotkey", "ctrl+shift+f"),
            transform=config.get("selection_transform_hotkey", ""),
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
        whispercpp.server.stop()
        llamacpp.server.stop()
        self._qt_app.quit()

    # --- Push-to-talk pipeline ------------------------------------------

    def _start_recording(self, mode: str) -> bool:
        """Keyboard thread. Start the shared recorder unless it's busy."""
        with self._ptt_lock:
            if self._ptt_active:
                return False
            try:
                self.recorder.start()
            except MicNotFoundError:
                self.overlay.show_error_signal.emit("No microphone detected")
                return False
            except Exception as e:
                self.overlay.show_error_signal.emit(str(e))
                return False
            self._ptt_active = True
            self._ptt_mode = mode
        return True

    def _stop_recording(self, mode: str) -> bytes | None:
        """Keyboard thread. Stop a recording started for `mode`; returns the
        WAV, or None if there's nothing (long enough) to process."""
        with self._ptt_lock:
            if not self._ptt_active or self._ptt_mode != mode:
                return None
            self._ptt_active = False
            self._ptt_mode = None

        try:
            wav_bytes, duration = self.recorder.stop()
        except Exception as e:
            self.overlay.show_error_signal.emit(str(e))
            return None

        min_dur = float(config.get("min_recording_seconds", 0.5))
        if duration < min_dur or not wav_bytes:
            self.overlay.hide_signal.emit()
            return None
        return wav_bytes

    def _on_ptt_press(self):
        """Keyboard thread. Start recording, flash overlay."""
        if config.missing_api_key:
            self.overlay.show_error_signal.emit("No API key — open Settings")
            return
        if self._start_recording("dictation"):
            self.overlay.show_recording_signal.emit()

    def _on_ptt_release(self):
        """Keyboard thread. Stop recording, spawn worker to transcribe/process/inject."""
        wav_bytes = self._stop_recording("dictation")
        if wav_bytes is None:
            return

        self.overlay.show_processing_signal.emit("Transcribing")
        worker = threading.Thread(
            target=self._process_recording, args=(wav_bytes,), daemon=True
        )
        worker.start()

    def _on_transform_press(self):
        """Keyboard thread. Selection Transform: record the spoken instruction."""
        if config.missing_transcription_key or config.missing_ai_key:
            self.overlay.show_error_signal.emit("No API key — open Settings")
            return
        if self._start_recording("transform"):
            self.overlay.show_transform_signal.emit()

    def _on_transform_release(self):
        wav_bytes = self._stop_recording("transform")
        if wav_bytes is None:
            return

        self.overlay.show_processing_signal.emit("Transcribing")
        worker = threading.Thread(
            target=selection_transform.run,
            args=(wav_bytes, self.overlay),
            daemon=True,
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

        ai_enabled = bool(config.get("ai_cleanup_enabled", True))
        if ai_enabled:
            self.overlay.show_processing_signal.emit("AI processing")
        try:
            cleaned = cleanup(raw)
        except AIError as e:
            transcript_log.record(raw, None)
            self.overlay.show_error_signal.emit(f"AI failed: {e}")
            return

        transcript_log.record(raw, cleaned if ai_enabled else None)

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
        self.recorder.set_device(config.get("input_device") or None)
        self.tray.refresh_menu()
        self.tray.refresh_icon()
        self._apply_whisper_server()
        self._apply_llama_server()

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

    if (
        sys.platform.startswith("linux")
        and os.environ.get("WAYLAND_DISPLAY")
        and os.environ.get("DISPLAY")
    ):
        # Native Wayland doesn't let clients position their own windows or
        # keep them above others, so the overlay would land at a random spot.
        # Run Qt through XWayland instead (overridable via QT_QPA_PLATFORM).
        os.environ.setdefault("QT_QPA_PLATFORM", "xcb")

    qt_app = QApplication(sys.argv)
    qt_app.setQuitOnLastWindowClosed(False)
    qt_app.setAttribute(Qt.ApplicationAttribute.AA_DontShowIconsInMenus, False)

    controller = App(qt_app)
    controller.start()

    if not _is_admin():
        # Non-fatal: surface via overlay after a moment so users know.
        # Many apps still work without admin; games/UAC-elevated apps won't.
        pass

    # Ctrl+C / SIGTERM: quit cleanly instead of raising KeyboardInterrupt in
    # whatever slot happens to run. A second Ctrl+C kills immediately.
    def _on_signal(signum, _frame):
        signal.signal(signum, signal.SIG_DFL)
        reason = (
            "Interrupted" if signum == signal.SIGINT else signal.Signals(signum).name
        )
        print(f"\nFlowClone: {reason}, shutting down.", file=sys.stderr, flush=True)
        controller._quit()

    signal.signal(signal.SIGINT, _on_signal)
    signal.signal(signal.SIGTERM, _on_signal)
    # Python only runs signal handlers between bytecodes; the Qt event loop is
    # native code, so wake the interpreter periodically.
    signal_timer = QTimer()
    signal_timer.timeout.connect(lambda: None)
    signal_timer.start(200)

    rc = qt_app.exec()
    try:
        controller._quit()
    except Exception:
        pass
    sys.exit(rc)


if __name__ == "__main__":
    main()
