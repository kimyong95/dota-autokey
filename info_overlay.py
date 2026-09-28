"""One line of text over Dota, just above the HUD, for SHOW_SECONDS; a newer line replaces it."""
from PySide6.QtCore import QRectF, Qt, QTimer, Signal, Slot
from PySide6.QtGui import QColor, QFont, QFontMetricsF

from window_utils import LogicalWindow

WIDTH, HEIGHT = 2400, 72        # logical (4K) pixels
BOTTOM = 700                    # logical pixels from the HUD's bottom edge to the line's
FONT_SIZE, PAD = 44, 18
TEXT = QColor(235, 235, 235)
BACKGROUND = QColor(0, 0, 0, 170)
SHOW_SECONDS = 3


class InfoOverlay(LogicalWindow):
    """Create on the Qt thread; show_line(text) from any thread."""
    line_requested = Signal(str)

    def __init__(self):
        super().__init__(QRectF(0, 0, WIDTH, HEIGHT))
        self.font = QFont("Consolas")
        self.font.setPixelSize(FONT_SIZE)
        self.line = ""
        self.hide_timer = QTimer(self)
        self.hide_timer.setSingleShot(True)
        self.hide_timer.timeout.connect(self.hide)
        self.line_requested.connect(self.set_line, Qt.QueuedConnection)
        self.follow_hud(BOTTOM)

    def show_line(self, text):
        self.line_requested.emit(text)

    @Slot(str)
    def set_line(self, text):
        self.line = text
        self.show()
        self.update()
        self.hide_timer.start(SHOW_SECONDS * 1000)      # restarts the countdown for a newer line

    def paintEvent(self, event):
        painter = self.logical_painter()
        painter.setFont(self.font)
        width = QFontMetricsF(self.font).horizontalAdvance(self.line) + 2 * PAD
        box = QRectF((WIDTH - width) / 2, 0, width, HEIGHT)
        painter.fillRect(box, BACKGROUND)
        painter.setPen(TEXT)
        painter.drawText(box, Qt.AlignCenter, self.line)
