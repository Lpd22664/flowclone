"""Always-on-top floating status overlay.

Glass pill, bottom-center. Content:  [ status dot | visualiser | label ]

IMPORTANT — three invariants preventing past bugs:

1. We paint the drop shadow ourselves (not QGraphicsDropShadowEffect). Stacking
   a shadow effect on the inner pill + an opacity effect on the outer widget
   triggered Qt's "Painter not active" / QWidgetEffectSourcePrivate::pixmap
   cascade (nested effects render each other's subtrees recursively).

2. Fade is done via QGraphicsOpacityEffect (not setWindowOpacity). Layered
   windows on Windows 11 DWM render a 1px outline around their full bounding
   rectangle — visible as a "transparent box with a border" around the pill.
   A single graphics effect (with no other effects in the tree) avoids layered
   mode and has no nesting conflict.

3. After every show() we re-assert HWND_TOPMOST via SetWindowPos. Qt's
   WindowStaysOnTopHint is honored at widget-create time, but topmost is a
   first-come-first-served z-ordering in Windows — if another topmost window
   (some terminals, tooltips, screen-capture frames) claims the topmost slot
   after us, we slip behind it. Console windows (cmd, PowerShell, Windows
   Terminal) are the main repro: the overlay would just never become visible.
   Native SetWindowPos on each show puts us back on top.
"""
from __future__ import annotations

import ctypes
import sys

from PyQt6.QtCore import (
    QEasingCurve,
    QPoint,
    QPropertyAnimation,
    QRectF,
    Qt,
    QTimer,
    pyqtSignal,
    pyqtSlot,
)
from PyQt6.QtGui import QColor, QGuiApplication, QPainter, QPen
from PyQt6.QtWidgets import (
    QGraphicsOpacityEffect,
    QHBoxLayout,
    QLabel,
    QWidget,
)

import debug_log
from visualiser import BarVisualiser, MODE_DONE, MODE_IDLE, MODE_LISTEN, MODE_PROCESS


# --- Native topmost (Windows only) --------------------------------------
# See invariant 3 in the module docstring.

if sys.platform == "win32":
    from ctypes import wintypes

    _user32 = ctypes.WinDLL("user32", use_last_error=True)
    _user32.SetWindowPos.argtypes = [
        wintypes.HWND, wintypes.HWND,
        ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
        wintypes.UINT,
    ]
    _user32.SetWindowPos.restype = wintypes.BOOL

    _HWND_TOPMOST = -1
    _SWP_NOSIZE = 0x0001
    _SWP_NOMOVE = 0x0002
    _SWP_NOACTIVATE = 0x0010
    _SWP_SHOWWINDOW = 0x0040
    _SWP_FLAGS = _SWP_NOSIZE | _SWP_NOMOVE | _SWP_NOACTIVATE | _SWP_SHOWWINDOW

    def _assert_topmost(hwnd_int: int) -> None:
        try:
            _user32.SetWindowPos(
                wintypes.HWND(hwnd_int),
                wintypes.HWND(_HWND_TOPMOST),
                0, 0, 0, 0, _SWP_FLAGS,
            )
        except Exception:
            pass
else:
    def _assert_topmost(hwnd_int: int) -> None:
        pass


# --- Design tokens ------------------------------------------------------

OVERLAY_W = 284
OVERLAY_H = 48
SHADOW_PADDING = 22     # pixels around pill for shadow spread
MARGIN_BOTTOM = 42
CORNER_RADIUS = 14

BG_COLOR = QColor(17, 18, 20, 235)          # near-black, slightly translucent
BORDER_COLOR = QColor(255, 255, 255, 26)     # 10% white hairline

# Shadow tuning: y-offset + soft multi-layer falloff
SHADOW_Y_OFFSET = 10

STATE_HIDDEN = "hidden"
STATE_RECORDING = "recording"
STATE_PROCESSING = "processing"
STATE_DONE = "done"
STATE_COMMAND = "command"
STATE_ERROR = "error"

STATE_META = {
    STATE_RECORDING: {"dot": "#ff453a", "bar": "#ffffff", "mode": MODE_LISTEN,  "label": "Listening"},
    STATE_PROCESSING: {"dot": "#9a9aa2", "bar": "#c7c7d1", "mode": MODE_PROCESS, "label": "Thinking"},
    STATE_DONE:       {"dot": "#30d158", "bar": "#30d158", "mode": MODE_DONE,    "label": "Done"},
    STATE_COMMAND:    {"dot": "#0a84ff", "bar": "#6ab0ff", "mode": MODE_LISTEN,  "label": "Command"},
    STATE_ERROR:      {"dot": "#ff9f0a", "bar": "#ff9f0a", "mode": MODE_IDLE,    "label": "Error"},
}


# --- Atoms --------------------------------------------------------------

class StatusDot(QWidget):
    """Solid colored dot, 8px. State-driven color; no animation."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(10, 10)
        self._color = QColor("#9a9aa2")

    def set_color(self, hex_str: str):
        self._color = QColor(hex_str)
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        p.setBrush(self._color)
        p.setPen(Qt.PenStyle.NoPen)
        p.drawEllipse(self.rect().adjusted(1, 1, -1, -1))


# --- Overlay ------------------------------------------------------------

class Overlay(QWidget):
    show_recording_signal = pyqtSignal()
    show_processing_signal = pyqtSignal()
    show_done_signal = pyqtSignal()
    show_command_signal = pyqtSignal()
    show_error_signal = pyqtSignal(str)
    hide_signal = pyqtSignal()
    amplitude_signal = pyqtSignal(float)

    def __init__(self):
        super().__init__()
        self.setWindowFlags(
            Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.Tool
            | Qt.WindowType.NoDropShadowWindowHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)

        self.setFixedSize(
            OVERLAY_W + SHADOW_PADDING * 2,
            OVERLAY_H + SHADOW_PADDING * 2,
        )

        # Layout — margins account for shadow padding + pill inner padding
        layout = QHBoxLayout(self)
        layout.setContentsMargins(
            SHADOW_PADDING + 14, SHADOW_PADDING + 10,
            SHADOW_PADDING + 16, SHADOW_PADDING + 10,
        )
        layout.setSpacing(10)

        self._dot = StatusDot(self)
        layout.addWidget(self._dot, alignment=Qt.AlignmentFlag.AlignVCenter)

        self._viz = BarVisualiser(self)
        self._viz.setMinimumWidth(140)
        layout.addWidget(self._viz, stretch=1)

        self._label = QLabel("", self)
        self._label.setStyleSheet(
            "color: rgba(255,255,255,210);"
            " font-family: 'Segoe UI Variable Text', 'Segoe UI', 'Inter', 'SF Pro Text', sans-serif;"
            " font-size: 12px; letter-spacing: 0.2px;"
            " background: transparent;"
        )
        self._label.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self._label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self._label.setMinimumWidth(64)
        layout.addWidget(self._label, alignment=Qt.AlignmentFlag.AlignVCenter)

        # Fade via QGraphicsOpacityEffect (not setWindowOpacity — see module
        # docstring). Single effect in the widget tree → no nesting conflict.
        self._opacity = QGraphicsOpacityEffect(self)
        self._opacity.setOpacity(0.0)
        self.setGraphicsEffect(self._opacity)

        self._fade = QPropertyAnimation(self._opacity, b"opacity", self)
        self._fade.setEasingCurve(QEasingCurve.Type.OutCubic)

        self._slide = QPropertyAnimation(self, b"pos", self)
        self._slide.setEasingCurve(QEasingCurve.Type.OutBack)

        self._hide_timer = QTimer(self)
        self._hide_timer.setSingleShot(True)
        self._hide_timer.timeout.connect(self._start_fade_out)

        self._state = STATE_HIDDEN
        self._resting_pos = QPoint(0, 0)
        self._compute_position()

        # Route signals from worker threads to slots on main thread.
        self.show_recording_signal.connect(self._on_recording)
        self.show_processing_signal.connect(self._on_processing)
        self.show_done_signal.connect(self._on_done)
        self.show_command_signal.connect(self._on_command)
        self.show_error_signal.connect(self._on_error)
        self.hide_signal.connect(self._start_fade_out)
        self.amplitude_signal.connect(self._on_amplitude)

    # --- Paint ----------------------------------------------------------

    def paintEvent(self, _):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setPen(Qt.PenStyle.NoPen)

        sp = SHADOW_PADDING

        # Shadow: concentric rounded rects, faint → strong as we approach the pill.
        # Stepping by 2 gives smooth enough falloff for a 22px padding.
        for i in range(sp, 0, -2):
            alpha = max(2, int(62 - i * 2.6))
            painter.setBrush(QColor(0, 0, 0, alpha))
            shadow_rect = QRectF(
                sp - i,
                sp - i + SHADOW_Y_OFFSET,
                OVERLAY_W + 2 * i,
                OVERLAY_H + 2 * i,
            )
            painter.drawRoundedRect(
                shadow_rect,
                CORNER_RADIUS + i,
                CORNER_RADIUS + i,
            )

        # Pill fill
        pill_rect = QRectF(sp, sp, OVERLAY_W, OVERLAY_H)
        painter.setBrush(BG_COLOR)
        painter.drawRoundedRect(pill_rect, CORNER_RADIUS, CORNER_RADIUS)

        # Hairline border
        painter.setBrush(Qt.BrushStyle.NoBrush)
        pen = QPen(BORDER_COLOR)
        pen.setWidthF(1.0)
        painter.setPen(pen)
        border_rect = QRectF(sp + 0.5, sp + 0.5, OVERLAY_W - 1, OVERLAY_H - 1)
        painter.drawRoundedRect(
            border_rect,
            CORNER_RADIUS - 0.5,
            CORNER_RADIUS - 0.5,
        )

    # --- Positioning ----------------------------------------------------

    def _compute_position(self):
        screen = QGuiApplication.primaryScreen()
        if screen is None:
            return
        geo = screen.availableGeometry()
        x = geo.x() + (geo.width() - self.width()) // 2
        y = geo.bottom() - self.height() - MARGIN_BOTTOM
        self._resting_pos = QPoint(x, y)
        self.move(self._resting_pos)

    # --- Show / hide ----------------------------------------------------

    def _start_show(self):
        self._compute_position()

        # Slide-up entry: start 12px lower than resting, animate to resting.
        start_pos = QPoint(self._resting_pos.x(), self._resting_pos.y() + 12)
        self.move(start_pos)

        if not self.isVisible():
            self._opacity.setOpacity(0.0)
            self.show()

        # Invariant 3: re-assert HWND_TOPMOST. Qt sometimes doesn't win the
        # z-order race against terminal/console windows on Windows.
        try:
            _assert_topmost(int(self.winId()))
        except Exception:
            pass

        debug_log.log("overlay.show", state=self._state)

        self._fade.stop()
        self._fade.setDuration(180)
        self._fade.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._fade.setStartValue(self._opacity.opacity())
        self._fade.setEndValue(1.0)
        self._fade.start()

        self._slide.stop()
        self._slide.setDuration(360)
        self._slide.setEasingCurve(QEasingCurve.Type.OutBack)
        self._slide.setStartValue(start_pos)
        self._slide.setEndValue(self._resting_pos)
        self._slide.start()

    def _start_fade_out(self):
        if self._state == STATE_HIDDEN:
            return
        self._fade.stop()
        self._fade.setDuration(260)
        self._fade.setEasingCurve(QEasingCurve.Type.InCubic)
        self._fade.setStartValue(self._opacity.opacity())
        self._fade.setEndValue(0.0)

        # Small drift-down on exit
        start_pos = self.pos()
        end_pos = QPoint(start_pos.x(), start_pos.y() + 8)
        self._slide.stop()
        self._slide.setDuration(260)
        self._slide.setEasingCurve(QEasingCurve.Type.InCubic)
        self._slide.setStartValue(start_pos)
        self._slide.setEndValue(end_pos)

        def _after():
            if self._opacity.opacity() <= 0.02:
                self.hide()
                self._state = STATE_HIDDEN
                self._viz.set_mode(MODE_IDLE)

        try:
            self._fade.finished.disconnect()
        except TypeError:
            pass
        self._fade.finished.connect(_after)
        self._fade.start()
        self._slide.start()

    # --- State transitions ---------------------------------------------

    def _apply_state(self, state: str, label_override: str | None = None):
        meta = STATE_META.get(state)
        if meta is None:
            return
        self._hide_timer.stop()
        self._state = state

        self._dot.set_color(meta["dot"])
        self._viz.set_color(meta["bar"])
        self._viz.set_mode(meta["mode"])
        self._label.setText(label_override if label_override is not None else meta["label"])

        if not self.isVisible() or self._opacity.opacity() < 0.5:
            self._start_show()

    @pyqtSlot()
    def _on_recording(self):
        self._apply_state(STATE_RECORDING)

    @pyqtSlot()
    def _on_processing(self):
        self._apply_state(STATE_PROCESSING)

    @pyqtSlot()
    def _on_done(self):
        self._apply_state(STATE_DONE)
        self._hide_timer.start(1200)

    @pyqtSlot()
    def _on_command(self):
        self._apply_state(STATE_COMMAND)

    @pyqtSlot(str)
    def _on_error(self, msg: str):
        short = (msg or "Error").strip()
        if len(short) > 26:
            short = short[:23] + "…"
        self._apply_state(STATE_ERROR, label_override=short)
        self._hide_timer.start(3000)

    @pyqtSlot(float)
    def _on_amplitude(self, rms: float):
        self._viz.push_level(rms)
