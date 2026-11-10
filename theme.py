"""Colour scheme selection shared by the settings window and the overlay."""

from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QGuiApplication

from config import config

SCHEME_SYSTEM = "system"
SCHEME_DARK = "dark"
SCHEME_LIGHT = "light"

# (value, label) for the settings combo box.
SCHEMES = (
    (SCHEME_SYSTEM, "Follow system"),
    (SCHEME_DARK, "Dark"),
    (SCHEME_LIGHT, "Light"),
)


def effective_scheme(choice: str | None = None) -> str:
    """Resolve a scheme setting to "dark" or "light".

    `choice` defaults to the saved "color_scheme" setting. "system" follows the
    desktop's light/dark preference and falls back to dark when it's unknown.
    """
    if choice is None:
        choice = config.get("color_scheme", SCHEME_SYSTEM)
    if choice in (SCHEME_DARK, SCHEME_LIGHT):
        return choice
    hints = QGuiApplication.styleHints()
    if hints is not None and hints.colorScheme() == Qt.ColorScheme.Light:
        return SCHEME_LIGHT
    return SCHEME_DARK
