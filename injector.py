"""Clipboard-based text injection. Saves, overwrites, pastes, restores."""
import time

import keyboard
import pyperclip


def inject_text(text: str):
    if not text or not text.strip():
        return
    try:
        original = pyperclip.paste()
    except Exception:
        original = ""

    pyperclip.copy(text)
    time.sleep(0.05)
    keyboard.send("ctrl+v")
    time.sleep(0.1)

    try:
        pyperclip.copy(original)
    except Exception:
        pass


def copy_selection() -> str:
    """Simulate Ctrl+C and read the clipboard. Returns selected text or empty string."""
    try:
        before = pyperclip.paste()
    except Exception:
        before = ""

    pyperclip.copy("")
    time.sleep(0.05)
    keyboard.send("ctrl+c")
    time.sleep(0.15)

    try:
        selected = pyperclip.paste()
    except Exception:
        selected = ""

    try:
        pyperclip.copy(before)
    except Exception:
        pass

    return selected or ""
