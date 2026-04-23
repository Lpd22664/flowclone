"""Command Mode: user selects text, presses hotkey, speaks a transform command."""
from __future__ import annotations

import threading

from config import config
from audio import record_until_silence, MicNotFoundError
from transcription import transcribe, TranscriptionError
from ai_processor import apply_command, AIError
from injector import copy_selection, inject_text


class CommandModeController:
    """Toggle-based controller. First press starts listening; second press stops early."""

    def __init__(self, overlay, on_error):
        self._overlay = overlay
        self._on_error = on_error
        self._stop_event: threading.Event | None = None
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()

    @property
    def active(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def toggle(self):
        with self._lock:
            if self.active:
                if self._stop_event is not None:
                    self._stop_event.set()
                return
            if not config.api_key:
                self._overlay.show_error_signal.emit("No API key — open Settings")
                return
            self._stop_event = threading.Event()
            self._thread = threading.Thread(
                target=self._run, args=(self._stop_event,), daemon=True
            )
            self._thread.start()

    def _run(self, stop_event: threading.Event):
        overlay = self._overlay

        # Capture current selection BEFORE entering listening state, so the
        # user's selection isn't lost if they accidentally click away.
        selected = ""
        try:
            selected = copy_selection()
        except Exception:
            selected = ""

        overlay.show_command_signal.emit()

        silence_s = float(config.get("command_mode_silence_seconds", 1.5))
        silence_t = float(config.get("command_mode_silence_threshold", 0.01))

        try:
            wav_bytes, duration = record_until_silence(
                silence_seconds=silence_s,
                silence_threshold=silence_t,
                stop_event=stop_event,
                on_level=overlay.amplitude_signal.emit,
            )
        except MicNotFoundError:
            overlay.show_error_signal.emit("No microphone detected")
            return
        except Exception as e:
            overlay.show_error_signal.emit(str(e))
            return

        if duration < float(config.get("min_recording_seconds", 0.5)) or not wav_bytes:
            overlay.hide_signal.emit()
            return

        overlay.show_processing_signal.emit()

        try:
            command_text = transcribe(wav_bytes)
        except TranscriptionError as e:
            overlay.show_error_signal.emit(f"Transcription failed: {e}")
            return

        if not command_text.strip():
            overlay.hide_signal.emit()
            return

        try:
            result = apply_command(selected, command_text)
        except AIError as e:
            overlay.show_error_signal.emit(f"AI failed: {e}")
            return

        try:
            inject_text(result)
        except Exception as e:
            overlay.show_error_signal.emit(f"Inject failed: {e}")
            return

        overlay.show_done_signal.emit()
