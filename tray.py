"""System tray icon. Runs pystray on a daemon thread; emits Qt signals on click."""
from __future__ import annotations

import sys
import threading
from typing import Callable

from PIL import Image, ImageDraw
import pystray
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QAction, QColor, QIcon, QPainter, QPen, QPixmap
from PyQt6.QtWidgets import QMenu, QSystemTrayIcon

from config import config
import elevation


def _build_icon_image(size: int = 64) -> Image.Image:
    """Render a clean, thin-stroke mic glyph on a transparent background.

    Transparent background reads better than a filled circle in both light
    and dark Windows tray themes. Strokes are widened to stay legible at 16×16.
    """
    # Draw at 4x then downsample — gives us antialiased strokes at any tray size.
    scale = 4
    S = size * scale
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    white = (240, 240, 244, 255)
    stroke = max(2, int(S * 0.07))
    half = stroke / 2

    cx = S / 2
    # Capsule body
    body_w = S * 0.36
    body_top = S * 0.18
    body_bot = S * 0.60
    draw.rounded_rectangle(
        (cx - body_w / 2, body_top, cx + body_w / 2, body_bot),
        radius=body_w / 2,
        outline=white,
        width=stroke,
    )

    # U-shaped stand
    stand_w = S * 0.56
    stand_top = S * 0.42
    stand_bot = S * 0.74
    # left vertical
    draw.line(
        (cx - stand_w / 2, stand_top, cx - stand_w / 2, stand_bot - stand_w / 2 * 0.45),
        fill=white, width=stroke,
    )
    # right vertical
    draw.line(
        (cx + stand_w / 2, stand_top, cx + stand_w / 2, stand_bot - stand_w / 2 * 0.45),
        fill=white, width=stroke,
    )
    # bottom arc
    arc_box = (
        cx - stand_w / 2, stand_bot - stand_w / 2,
        cx + stand_w / 2, stand_bot + stand_w / 2,
    )
    draw.arc(arc_box, start=0, end=180, fill=white, width=stroke)

    # stem
    stem_top = stand_bot + stand_w / 4
    stem_bot = S * 0.88
    draw.line((cx, stem_top, cx, stem_bot), fill=white, width=stroke)

    # base
    base_half = S * 0.13
    draw.line(
        (cx - base_half, stem_bot, cx + base_half, stem_bot),
        fill=white, width=stroke,
    )

    return img.resize((size, size), Image.LANCZOS)


class Tray:
    def __init__(
        self,
        on_open_settings: Callable[[], None],
        on_toggle_ai_cleanup: Callable[[bool], None],
        on_quit: Callable[[], None],
        on_relaunch_admin: Callable[[], None] | None = None,
    ):
        self._on_open_settings = on_open_settings
        self._on_toggle_ai_cleanup = on_toggle_ai_cleanup
        self._on_quit = on_quit
        self._on_relaunch_admin = on_relaunch_admin or elevation.relaunch_as_admin
        self._icon: pystray.Icon | None = None
        self._qt_tray: QSystemTrayIcon | None = None
        self._qt_menu: QMenu | None = None
        self._thread: threading.Thread | None = None

    def _menu(self) -> pystray.Menu:
        def ai_enabled(_):
            return bool(config.get("ai_cleanup_enabled", True))

        def toggle_ai(_icon, item):
            new = not bool(config.get("ai_cleanup_enabled", True))
            config.set("ai_cleanup_enabled", new)
            config.save()
            self._on_toggle_ai_cleanup(new)

        def open_settings(_icon, _item):
            self._on_open_settings()

        def quit_app(_icon, _item):
            self._on_quit()
            if self._icon is not None:
                self._icon.stop()

        def run_as_admin(_icon, _item):
            # Launch elevated, then exit this non-elevated instance so the
            # user doesn't end up with two tray icons.
            if self._on_relaunch_admin():
                self._on_quit()
                if self._icon is not None:
                    self._icon.stop()

        is_admin = elevation.is_elevated()
        header = "FlowClone (admin)" if is_admin else "FlowClone"

        items = [
            pystray.MenuItem(header, None, enabled=False),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Settings", open_settings, default=True),
            pystray.MenuItem("Toggle AI Cleanup", toggle_ai, checked=ai_enabled),
        ]
        if sys.platform == "win32" and not is_admin:
            # Only show when non-admin — the option to re-elevate is only
            # meaningful when we currently can't inject into elevated targets.
            items.append(pystray.Menu.SEPARATOR)
            items.append(pystray.MenuItem("Run as administrator", run_as_admin))
        items.append(pystray.Menu.SEPARATOR)
        items.append(pystray.MenuItem("Quit", quit_app))

        return pystray.Menu(*items)

    def start(self):
        if sys.platform != "win32":
            self._start_qt_tray()
            return

        image = _build_icon_image()
        self._icon = pystray.Icon(
            "FlowClone",
            image,
            "FlowClone",
            self._menu(),
        )
        self._thread = threading.Thread(target=self._icon.run, daemon=True)
        self._thread.start()

    def _start_qt_tray(self):
        if not QSystemTrayIcon.isSystemTrayAvailable():
            return
        self._qt_menu = self._build_qt_menu()
        self._qt_tray = QSystemTrayIcon(_build_qt_icon(), None)
        self._qt_tray.setToolTip("FlowClone")
        self._qt_tray.setContextMenu(self._qt_menu)
        self._qt_tray.show()

    def _build_qt_menu(self) -> QMenu:
        menu = QMenu()
        header = QAction("FlowClone", menu)
        header.setEnabled(False)
        menu.addAction(header)
        menu.addSeparator()

        settings = QAction("Settings", menu)
        settings.triggered.connect(lambda: self._on_open_settings())
        menu.addAction(settings)

        ai = QAction("Toggle AI Cleanup", menu)
        ai.setCheckable(True)
        ai.setChecked(bool(config.get("ai_cleanup_enabled", True)))

        def toggle_ai():
            new = not bool(config.get("ai_cleanup_enabled", True))
            config.set("ai_cleanup_enabled", new)
            config.save()
            ai.setChecked(new)
            self._on_toggle_ai_cleanup(new)

        ai.triggered.connect(toggle_ai)
        menu.addAction(ai)
        menu.addSeparator()

        quit_action = QAction("Quit", menu)
        quit_action.triggered.connect(lambda: self._on_quit())
        menu.addAction(quit_action)
        return menu

    def stop(self):
        if self._icon is not None:
            try:
                self._icon.stop()
            except Exception:
                pass
        self._icon = None
        if self._qt_tray is not None:
            self._qt_tray.hide()
        self._qt_tray = None
        self._qt_menu = None

    def refresh_menu(self):
        if self._qt_tray is not None:
            self._qt_menu = self._build_qt_menu()
            self._qt_tray.setContextMenu(self._qt_menu)
            return
        if self._icon is not None:
            self._icon.menu = self._menu()
            try:
                self._icon.update_menu()
            except Exception:
                pass


def _build_qt_icon(size: int = 64) -> QIcon:
    pix = QPixmap(size, size)
    pix.fill(Qt.GlobalColor.transparent)
    p = QPainter(pix)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    pen = QPen(QColor(240, 240, 244), max(2, int(size * 0.07)))
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    p.setPen(pen)

    cx = size / 2
    body_w = size * 0.36
    body_top = size * 0.18
    body_bot = size * 0.60
    p.drawRoundedRect(
        int(cx - body_w / 2),
        int(body_top),
        int(body_w),
        int(body_bot - body_top),
        int(body_w / 2),
        int(body_w / 2),
    )

    stand_w = size * 0.56
    stand_top = size * 0.42
    stand_bot = size * 0.74
    p.drawLine(
        int(cx - stand_w / 2),
        int(stand_top),
        int(cx - stand_w / 2),
        int(stand_bot - stand_w / 2 * 0.45),
    )
    p.drawLine(
        int(cx + stand_w / 2),
        int(stand_top),
        int(cx + stand_w / 2),
        int(stand_bot - stand_w / 2 * 0.45),
    )
    p.drawArc(
        int(cx - stand_w / 2),
        int(stand_bot - stand_w / 2),
        int(stand_w),
        int(stand_w),
        0,
        180 * 16,
    )
    stem_top = stand_bot + stand_w / 4
    stem_bot = size * 0.88
    p.drawLine(int(cx), int(stem_top), int(cx), int(stem_bot))
    base_half = size * 0.13
    p.drawLine(int(cx - base_half), int(stem_bot), int(cx + base_half), int(stem_bot))
    p.end()
    return QIcon(pix)
