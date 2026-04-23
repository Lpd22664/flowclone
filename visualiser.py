"""Real-time bar visualiser for the overlay.

- listen mode: bars driven by live RMS amplitudes
- process mode: synthetic sine-wave shimmer sweeps through bars
- done/idle: bars ease to zero
"""
from __future__ import annotations

import math
import time

from PyQt6.QtCore import QRectF, Qt, QTimer
from PyQt6.QtGui import QColor, QPainter
from PyQt6.QtWidgets import QWidget

MODE_IDLE = "idle"
MODE_LISTEN = "listen"
MODE_PROCESS = "process"
MODE_DONE = "done"

# Per-bar heterogeneity so bars don't all move in lockstep. Arbitrary-looking
# multipliers sampled once — do NOT randomise at runtime, the visualiser must
# be deterministic and calm.
BAR_WEIGHTS = [
    0.62, 0.88, 1.05, 0.76, 1.18, 0.94, 1.22, 1.00,
    1.00, 1.22, 0.94, 1.18, 0.76, 1.05, 0.88, 0.62,
]
BAR_COUNT = len(BAR_WEIGHTS)


class BarVisualiser(QWidget):
    """16 rounded bars, center-mirrored. Draw height expresses current amplitude."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setMinimumSize(160, 28)

        self._mode: str = MODE_IDLE
        self._targets = [0.0] * BAR_COUNT
        self._values = [0.0] * BAR_COUNT
        self._color = QColor("#ffffff")
        self._bar_width = 3
        self._bar_gap = 4
        self._start = time.monotonic()

        # Smoothing runs at ~60fps regardless of amplitude callback rate.
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(16)

    # --- Public API -----------------------------------------------------

    def set_mode(self, mode: str):
        if mode == self._mode:
            return
        self._mode = mode
        if mode == MODE_IDLE or mode == MODE_DONE:
            self._targets = [0.0] * BAR_COUNT
        self._start = time.monotonic()

    def set_color(self, hex_str: str):
        self._color = QColor(hex_str)

    def push_level(self, rms: float):
        """Called from main thread (via Qt signal). Maps RMS 0..1 → target heights."""
        if self._mode != MODE_LISTEN:
            return
        # Perceptual scaling: sqrt + gain. Real voice rarely exceeds ~0.25 RMS,
        # so we boost and clamp. The 4.5x gain is chosen empirically.
        magnitude = min(1.0, math.sqrt(max(0.0, rms)) * 2.2)
        for i, w in enumerate(BAR_WEIGHTS):
            self._targets[i] = max(0.08, min(1.0, magnitude * w))

    # --- Internals ------------------------------------------------------

    def _tick(self):
        if self._mode == MODE_PROCESS:
            self._update_process_targets()

        # Ease current values toward targets. Different ease rates for
        # rise vs fall so bars snap up quickly and decay gently.
        for i in range(BAR_COUNT):
            target = self._targets[i]
            current = self._values[i]
            if target > current:
                current += (target - current) * 0.45
            else:
                current += (target - current) * 0.12
            self._values[i] = current

        if self._mode in (MODE_IDLE, MODE_DONE):
            for i in range(BAR_COUNT):
                self._targets[i] = 0.0

        self.update()

    def _update_process_targets(self):
        """Smooth sweeping wave pattern — looks like 'thinking'."""
        t = time.monotonic() - self._start
        for i in range(BAR_COUNT):
            phase = (i / BAR_COUNT) * math.pi * 2 - t * 4.5
            wave = (math.sin(phase) + 1.0) / 2.0  # 0..1
            # Add a secondary ripple so it doesn't look mechanical
            ripple = 0.15 * math.sin(t * 3.1 + i * 0.7)
            self._targets[i] = max(0.08, min(1.0, 0.25 + wave * 0.55 + ripple))

    def paintEvent(self, _):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setPen(Qt.PenStyle.NoPen)

        w = self.width()
        h = self.height()
        total_bar_width = BAR_COUNT * self._bar_width + (BAR_COUNT - 1) * self._bar_gap
        x0 = (w - total_bar_width) / 2
        center_y = h / 2

        min_bar_h = 2.0
        max_bar_h = h - 2.0

        base_alpha = 235 if self._mode in (MODE_LISTEN, MODE_PROCESS) else 120
        for i, v in enumerate(self._values):
            bar_h = min_bar_h + (max_bar_h - min_bar_h) * max(0.0, min(1.0, v))
            x = x0 + i * (self._bar_width + self._bar_gap)
            y = center_y - bar_h / 2

            # Fade outer bars slightly so the visualiser feels centered
            edge_distance = abs(i - (BAR_COUNT - 1) / 2) / ((BAR_COUNT - 1) / 2)
            alpha = int(base_alpha * (1.0 - 0.25 * edge_distance))
            color = QColor(self._color)
            color.setAlpha(alpha)
            painter.setBrush(color)

            rect = QRectF(x, y, self._bar_width, bar_h)
            painter.drawRoundedRect(rect, self._bar_width / 2, self._bar_width / 2)
