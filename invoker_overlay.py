"""Invoker panel layout, painting, refresh, and Ctrl+= visibility toggle."""
import math
import signal
import urllib.request
from pathlib import Path
import keyboard
from PySide6.QtCore import QRectF, Qt, QTimer, Signal, Slot
from PySide6.QtGui import (QColor, QFont, QFontDatabase, QLinearGradient,
                           QPainter, QPainterPath, QPen, QPixmap)
from PySide6.QtWidgets import QApplication
from window_utils import LogicalWindow

ASSETS = Path(__file__).parent / "assets"
# 4K design units; the bottom edge touches the left quick-buy panel.
PANEL_WIDTH, QUICK_BUY_TOP = 582, 208
SIZE, PAD, FRAME = 76, 8, 4
PANEL_TOP = QColor(30, 36, 39, 155)
PANEL_BOTTOM = QColor(17, 22, 25, 185)
COOLDOWN_TINT = QColor(0, 0, 0, 185)

# Autokey trigger keys in panel order; invoke has none, and sits bottom-left.
# [q][w][e][r][o][p]
# [-]   [d][f][4][5]
LAYOUT = {
    "invoker_ice_wall":        (0, 0),
    "invoker_sun_strike":      (1, 0),
    "invoker_chaos_meteor":    (2, 0),
    "invoker_deafening_blast": (3, 0),
    "invoker_forge_spirit":    (2, 1),
    "invoker_alacrity":        (3, 1),
    "invoker_cold_snap":       (4, 0),
    "invoker_tornado":         (5, 0),
    "invoker_emp":             (4, 1),
    "invoker_ghost_walk":      (5, 1),
    "invoker_invoke":          (0, 1),
}


def load_font(path, size):
    font_id = QFontDatabase.addApplicationFont(str(path))
    families = QFontDatabase.applicationFontFamilies(font_id)
    font = QFont(families[0] if families else "Arial")
    font.setPixelSize(size)
    return font


def draw_panel(painter, rect):
    panel = QLinearGradient(0, rect.top(), 0, rect.bottom())
    panel.setColorAt(0, PANEL_TOP)
    panel.setColorAt(1, PANEL_BOTTOM)
    painter.fillRect(rect, panel)
    # Native HUD panels have a faint top lip, not a bright enclosing outline.
    painter.fillRect(QRectF(rect.left(), rect.top(), rect.width(), 2), QColor(137, 148, 151, 65))
    painter.fillRect(QRectF(rect.left(), rect.bottom() - 2, rect.width(), 2),
                     QColor(0, 0, 0, 110))


def draw_spell(painter, slot, icon, left=0, fraction=0, invoked=False, frame=4):
    painter.save()
    # Grey upper bevel, dark lower edge, and a thin recessed artwork outline.
    painter.fillRect(slot, QColor(5, 8, 10, 230))
    bevel = QLinearGradient(0, slot.top(), 0, slot.bottom())
    bevel.setColorAt(0, QColor(167, 174, 174, 245))
    bevel.setColorAt(0.45, QColor(102, 110, 113, 240))
    bevel.setColorAt(1, QColor(48, 54, 57, 240))
    painter.fillRect(slot.adjusted(2, 2, -2, -2), bevel)
    rect = slot.adjusted(frame, frame, -frame, -frame)
    painter.drawPixmap(rect, icon, QRectF(icon.rect()))
    if left > 0:
        # A clipped circular sector covers the remaining portion of the square.
        painter.fillRect(rect, QColor(0, 0, 0, 45))
        radius = math.hypot(rect.width(), rect.height()) / 2
        circle = QRectF(rect.center().x() - radius, rect.center().y() - radius,
                        2 * radius, 2 * radius)
        shadow = QPainterPath(rect.center())
        shadow.arcTo(circle, 90, 360 * fraction)
        shadow.closeSubpath()
        painter.save()
        painter.setClipRect(rect)
        painter.fillPath(shadow, COOLDOWN_TINT)
        painter.restore()
        text = str(math.ceil(left))
        painter.setPen(QColor(0, 0, 0, 230))
        painter.drawText(rect.translated(2, 2), Qt.AlignCenter, text)
        painter.setPen(QColor("#f2f2ed"))
        painter.drawText(rect, Qt.AlignCenter, text)
    painter.setPen(QPen(QColor(0, 0, 0, 125), 2))
    painter.setBrush(Qt.NoBrush)
    painter.drawRect(rect.adjusted(1, 1, -1, -1))

    if invoked:
        # Soft outer glow and a crisp green rim, confined to the existing slot.
        painter.setPen(QPen(QColor(111, 205, 93, 65), 6))
        painter.drawRect(slot.adjusted(3, 3, -3, -3))
        painter.setPen(QPen(QColor(137, 221, 108, 245), 2))
        painter.drawRect(slot.adjusted(2, 2, -2, -2))
    painter.restore()


class Overlay(LogicalWindow):
    toggle_requested = Signal()

    def __init__(self, get_state):
        slot = SIZE + 2 * FRAME
        step = slot + PAD
        margin = (PANEL_WIDTH - (6 * step - PAD)) / 2
        super().__init__(QRectF(0, 0, PANEL_WIDTH, 2 * step - PAD + 2 * margin))
        self.get_state = get_state
        self.state = {}
        self.spell_rects = {
            spell: QRectF(margin + col * step, margin + row * step, slot, slot)
            for spell, (col, row) in LAYOUT.items()
        }
        self.prepare_assets()
        self.icons = {spell: QPixmap(str(ASSETS / f"{spell}.png")) for spell in LAYOUT}
        self.number_font = load_font(ASSETS / "radiance-regular.otf", 38)
        self.toggle_requested.connect(self.toggle, Qt.QueuedConnection)
        self.follow_bottom(QUICK_BUY_TOP)
        timer = QTimer(self)
        timer.timeout.connect(self.refresh)
        timer.start(50)
        self.refresh()

    def prepare_assets(self):
        """Download missing spell icons before loading their pixmaps."""
        ASSETS.mkdir(parents=True, exist_ok=True)
        for spell in LAYOUT:
            target = ASSETS / f"{spell}.png"
            if target.exists():
                continue
            url = f"https://cdn.cloudflare.steamstatic.com/apps/dota2/images/dota_react/abilities/{spell}.png"
            with urllib.request.urlopen(url, timeout=15) as response:
                data = response.read()
            target.write_bytes(data)

    @Slot()
    def toggle(self):
        self.setVisible(not self.isVisible())
        if self.isVisible():
            self.align_to_display(force=True)

    def refresh(self):
        self.set_state(self.get_state())

    def set_state(self, state):
        """Accept {spell: (seconds_remaining, cooldown_fraction, invoked)}."""
        self.state = state
        self.update()

    def paintEvent(self, event):
        painter = self.logical_painter()
        painter.setRenderHint(QPainter.SmoothPixmapTransform)
        painter.setFont(self.number_font)
        draw_panel(painter, self.design_rect)
        for spell, rect in self.spell_rects.items():
            draw_spell(painter, rect, self.icons[spell], *self.state.get(spell, (0, 0, False)), frame=FRAME)


def start(get_state):
    """Blocks in the Qt event loop. Must be called on the main thread."""
    signal.signal(signal.SIGINT, signal.SIG_DFL)    # Qt's event loop swallows SIGINT otherwise
    qt = QApplication([])
    qt.setQuitOnLastWindowClosed(False)
    overlay = Overlay(get_state)
    hotkey = keyboard.add_hotkey("ctrl+=", overlay.toggle_requested.emit, suppress=True, trigger_on_release=True)
    try:
        qt.exec()
    finally:
        keyboard.remove_hotkey(hotkey)
