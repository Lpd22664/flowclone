"""System tray icon. Runs pystray on a daemon thread; emits Qt signals on click."""
from __future__ import annotations

import threading
from typing import Callable

from PIL import Image, ImageDraw
import pystray

from config import config


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
    ):
        self._on_open_settings = on_open_settings
        self._on_toggle_ai_cleanup = on_toggle_ai_cleanup
        self._on_quit = on_quit
        self._icon: pystray.Icon | None = None
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

        return pystray.Menu(
            pystray.MenuItem("FlowClone", None, enabled=False),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Settings", open_settings, default=True),
            pystray.MenuItem("Toggle AI Cleanup", toggle_ai, checked=ai_enabled),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Quit", quit_app),
        )

    def start(self):
        image = _build_icon_image()
        self._icon = pystray.Icon(
            "FlowClone",
            image,
            "FlowClone",
            self._menu(),
        )
        self._thread = threading.Thread(target=self._icon.run, daemon=True)
        self._thread.start()

    def stop(self):
        if self._icon is not None:
            try:
                self._icon.stop()
            except Exception:
                pass
        self._icon = None

    def refresh_menu(self):
        if self._icon is not None:
            self._icon.menu = self._menu()
            try:
                self._icon.update_menu()
            except Exception:
                pass
