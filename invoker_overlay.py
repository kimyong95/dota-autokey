"""Click-through cooldown overlay. Imported by invoker.py -- not meant to be run."""

import math
import signal
from pathlib import Path

from PySide6.QtCore import QRect, Qt, QTimer
from PySide6.QtGui import QColor, QFont, QGuiApplication, QPainter, QPixmap
from PySide6.QtWidgets import QApplication, QWidget

ASSETS = Path(__file__).parent / "assets"
SIZE, PAD = 58, 6
LEFT_MARGIN, BOTTOM_MARGIN = 10, 148        # gap from the screen's left and bottom edges
COOLDOWN_TINT = QColor(0, 0, 0, 190)        # dark backing behind the countdown digit

# [q][w][e][r][o][p]
#       [d][f][4][5]
LAYOUT = {                                  # spell -> (column, row)
    "invoker_ice_wall":        (0.0, 0),
    "invoker_sun_strike":      (1.0, 0),
    "invoker_chaos_meteor":    (2.0, 0),
    "invoker_deafening_blast": (3.0, 0),
    "invoker_forge_spirit":    (2.0, 1),
    "invoker_alacrity":        (3.0, 1),
    "invoker_cold_snap":       (4.0, 0),
    "invoker_tornado":         (5.0, 0),
    "invoker_emp":             (4.0, 1),
    "invoker_ghost_walk":      (5.0, 1),
}


def load_icon(spell, ratio):
    # scale in device pixels and tag the ratio, else Qt upscales a 64px icon on a HiDPI screen
    side = round(SIZE * ratio)
    icon = QPixmap(str(ASSETS / f"{spell}.png")).scaled(
        side, side, Qt.KeepAspectRatio, Qt.SmoothTransformation)
    icon.setDevicePixelRatio(ratio)
    return icon


class Overlay(QWidget):
    def __init__(self, remaining):
        super().__init__()
        self.remaining = remaining
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint
                            | Qt.Tool | Qt.WindowTransparentForInput)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.number_font = QFont()          # not self.font -- that shadows QWidget.font()
        self.number_font.setPixelSize(round(SIZE * 0.45))
        self.number_font.setBold(True)

        screen = QGuiApplication.primaryScreen()
        self.icons = {spell: load_icon(spell, screen.devicePixelRatio()) for spell in LAYOUT}
        columns = max(col for col, _ in LAYOUT.values()) + 1
        rows = max(row for _, row in LAYOUT.values()) + 1
        width = round(columns * (SIZE + PAD) - PAD)
        height = rows * (SIZE + PAD) - PAD
        bounds = screen.geometry()
        self.setGeometry(LEFT_MARGIN, bounds.height() - height - BOTTOM_MARGIN, width, height)

        timer = QTimer(self)
        timer.timeout.connect(self.update)
        timer.start(50)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setFont(self.number_font)
        for spell, (col, row) in LAYOUT.items():
            rect = QRect(round(col * (SIZE + PAD)), row * (SIZE + PAD), SIZE, SIZE)
            painter.drawPixmap(rect.topLeft(), self.icons[spell])
            left = self.remaining(spell)
            if left > 0:
                painter.fillRect(rect, COOLDOWN_TINT)
                painter.setPen(Qt.white)
                painter.drawText(rect, Qt.AlignCenter, str(math.ceil(left)))


def start(remaining):
    """Blocks in the Qt event loop. Must be called on the main thread."""
    if any(not (ASSETS / f"{spell}.png").exists() for spell in LAYOUT):
        raise SystemExit("missing icons -- run: uv run prepare_assets.py")
    signal.signal(signal.SIGINT, signal.SIG_DFL)    # Qt's event loop swallows SIGINT otherwise
    qt = QApplication([])
    overlay = Overlay(remaining)
    overlay.show()
    qt.exec()
