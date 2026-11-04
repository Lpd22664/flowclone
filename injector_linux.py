"""Text injection on Linux.

Linux counterpart of `injector.py`. Strategy: save clipboard, overwrite it,
send a synthetic paste shortcut, restore the clipboard.

Synthetic keys go through a uinput virtual keyboard (python-evdev), which
works under both X11 and Wayland. It needs write access to /dev/uinput
(normally via the `input` group or a udev ACL). The virtual device's name
is ignored by keyhook_linux so our own keystrokes never trigger hotkeys.

Terminal emulators paste with Ctrl+Shift+V rather than Ctrl+V. When the
focused window's class can be determined (xdotool on X11, kdotool on KDE
Wayland) we use the terminal shortcut; otherwise we fall back to Ctrl+V.

Clipboard access goes through pyperclip, which uses wl-clipboard on Wayland
and xclip/xsel on X11.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import threading
import time

import pyperclip
from evdev import UInput, ecodes as _e

import debug_log
from keyhook_linux import VIRTUAL_DEVICE_NAME


class ElevationRequired(Exception):
    """Kept for API parity with the Windows injector. Linux has no UIPI
    equivalent, so this is never raised here."""


# ---------------------------------------------------------------------------
# uinput virtual keyboard
# ---------------------------------------------------------------------------

_uinput: UInput | None = None
_uinput_lock = threading.Lock()


def _device() -> UInput:
    global _uinput
    with _uinput_lock:
        if _uinput is None:
            # Advertise the full standard key range so udev/libinput classify
            # the device as a real keyboard.
            keys = list(range(_e.KEY_ESC, _e.KEY_MICMUTE + 1))
            _uinput = UInput({_e.EV_KEY: keys}, name=VIRTUAL_DEVICE_NAME)
            # Give the compositor / X server time to pick up the new device,
            # otherwise the first keystrokes are dropped.
            time.sleep(0.5)
        return _uinput


def _send(*codes: int) -> None:
    ui = _device()
    for c in codes:
        ui.write(_e.EV_KEY, c, 1)
        ui.syn()
        time.sleep(0.008)
    for c in reversed(codes):
        ui.write(_e.EV_KEY, c, 0)
        ui.syn()
        time.sleep(0.008)


# ---------------------------------------------------------------------------
# Foreground window detection
# ---------------------------------------------------------------------------

_TERMINAL_CLASSES = {
    "konsole",
    "org.kde.konsole",
    "yakuake",
    "org.kde.yakuake",
    "gnome-terminal-server",
    "org.gnome.terminal",
    "org.gnome.console",
    "kgx",
    "org.gnome.ptyxis",
    "ptyxis",
    "xterm",
    "uxterm",
    "urxvt",
    "rxvt",
    "st",
    "st-256color",
    "xfce4-terminal",
    "mate-terminal",
    "lxterminal",
    "qterminal",
    "terminator",
    "tilix",
    "com.gexperts.tilix",
    "guake",
    "terminology",
    "alacritty",
    "kitty",
    "foot",
    "footclient",
    "wezterm",
    "org.wezfurlong.wezterm",
    "ghostty",
    "com.mitchellh.ghostty",
}


def _window_tool() -> str | None:
    session = os.environ.get("XDG_SESSION_TYPE", "").lower()
    if session == "wayland" or os.environ.get("WAYLAND_DISPLAY"):
        # Only KDE exposes the active window to external tools.
        return shutil.which("kdotool")
    return shutil.which("xdotool")


def _foreground_window_class() -> str:
    tool = _window_tool()
    if not tool:
        return ""
    try:
        out = subprocess.run(
            [tool, "getactivewindow", "getwindowclassname"],
            capture_output=True,
            text=True,
            timeout=1.0,
            check=False,
        )
        return out.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return ""


def _is_terminal(window_class: str) -> bool:
    return window_class.lower() in _TERMINAL_CLASSES


# ---------------------------------------------------------------------------
# Public API (same as injector.py)
# ---------------------------------------------------------------------------


def inject_text(text: str):
    """Paste `text` into the focused window via the clipboard."""
    if not text or not text.strip():
        return

    fg_class = _foreground_window_class()
    terminal = _is_terminal(fg_class)
    debug_log.log("inject.start", chars=len(text), fg_class=fg_class, terminal=terminal)

    try:
        original = pyperclip.paste()
    except Exception:
        original = ""

    pyperclip.copy(text)
    time.sleep(0.05)
    if terminal:
        _send(_e.KEY_LEFTCTRL, _e.KEY_LEFTSHIFT, _e.KEY_V)
    else:
        _send(_e.KEY_LEFTCTRL, _e.KEY_V)
    # Wayland/X11 clipboard reads are asynchronous; give the target app time
    # to fetch the data before we put the original contents back.
    time.sleep(0.3)

    try:
        pyperclip.copy(original)
    except Exception:
        pass
    debug_log.log("inject.clipboard_done")


def copy_selection() -> str:
    """Simulate copy and read the clipboard. Returns selected text or ''."""
    try:
        before = pyperclip.paste()
    except Exception:
        before = ""

    pyperclip.copy("")
    time.sleep(0.05)
    if _is_terminal(_foreground_window_class()):
        _send(_e.KEY_LEFTCTRL, _e.KEY_LEFTSHIFT, _e.KEY_C)
    else:
        _send(_e.KEY_LEFTCTRL, _e.KEY_C)
    time.sleep(0.2)

    try:
        selected = pyperclip.paste()
    except Exception:
        selected = ""

    try:
        pyperclip.copy(before)
    except Exception:
        pass

    return selected or ""
