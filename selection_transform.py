"""Selection Transform: the user selects text, holds the hotkey and speaks an
instruction. The selection is transformed by the AI using that (cleaned-up)
instruction as the prompt, and the result is put on the clipboard."""

from __future__ import annotations

import sys

import pyperclip

import transcript_log
from ai_processor import AIError, cleanup, cleanup_active, transform_selection
from hotkeys import wait_for_modifiers_released
from transcription import TranscriptionError, transcribe

if sys.platform == "win32":
    from injector import copy_selection
else:
    from injector_linux import copy_selection


def run(wav_bytes: bytes, overlay) -> None:
    """Worker thread, started once the hotkey has been released."""
    # Still-held hotkey modifiers would turn the synthetic Ctrl+C into e.g.
    # Ctrl+Shift+C, which isn't "copy" in most apps.
    wait_for_modifiers_released(timeout=2.0)
    try:
        selected = copy_selection()
    except Exception:
        selected = ""

    try:
        raw = transcribe(wav_bytes)
    except TranscriptionError as e:
        overlay.show_error_signal.emit(f"Transcription failed: {e}")
        return
    except Exception as e:
        overlay.show_error_signal.emit(str(e))
        return

    if not raw.strip():
        overlay.hide_signal.emit()
        return

    mode = transcript_log.MODE_TRANSFORM
    try:
        if cleanup_active():
            overlay.show_processing_signal.emit("AI processing")
        instruction = cleanup(raw)
        if not instruction.strip():
            transcript_log.record(raw, None, mode, selected)
            overlay.hide_signal.emit()
            return
        overlay.show_processing_signal.emit("Applying Prompt")
        result = transform_selection(selected, instruction)
    except AIError as e:
        transcript_log.record(raw, None, mode, selected)
        overlay.show_error_signal.emit(f"AI failed: {e}")
        return

    transcript_log.record(raw, result, mode, selected)

    if not result.strip():
        overlay.show_error_signal.emit("AI returned no text")
        return

    try:
        pyperclip.copy(result)
    except Exception as e:
        overlay.show_error_signal.emit(f"Clipboard failed: {e}")
        return

    overlay.show_toast_signal.emit("Copied")
