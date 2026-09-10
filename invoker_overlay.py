"""Invoker panel layout, painting, refresh, and Ctrl+= visibility toggle."""
import math
import signal
from functools import lru_cache
from pathlib import Path
import httpx
import keyboard
from PIL import Image, ImageDraw, ImageFilter, ImageFont
from PySide6.QtCore import QPointF, QRectF, Qt, QTimer, Signal, Slot
from PySide6.QtGui import (QColor, QImage, QLinearGradient, QPainter, QPen,
                           QPixmap, QPolygonF)
from PySide6.QtWidgets import QApplication
from window_utils import LogicalWindow

ASSETS = Path(__file__).parent / "assets"
# 4K design units; the bottom edge touches the left quick-buy panel.
PANEL_WIDTH, QUICK_BUY_TOP = 582, 208
# The frame is a black EDGE around a raised BEVEL; the slot stays 84 units wide,
# so widening it from 4 to 6 takes those units off the artwork instead.
SIZE, PAD, EDGE, BEVEL = 72, 8, 3, 3
FRAME = EDGE + BEVEL
PANEL_TOP = QColor(30, 36, 39, 155)
PANEL_BOTTOM = QColor(17, 22, 25, 185)
COOLDOWN_TINT = QColor(0, 0, 0, 185)
# Sampled off a 4K Dota ability slot: a near-black edge around a raised frame that
# runs bright silver on top, mid grey down the left, darker right, darkest bottom.
SLOT_EDGE = QColor(23, 27, 30, 240)
SLOT_EDGE_BOTTOM = QColor(15, 18, 20, 240)
# Outer and inner colour of each edge, clockwise from the top.
BEVEL_EDGES = ((QColor(138, 146, 153), QColor(107, 116, 124)),
               (QColor(43, 51, 59), QColor(58, 66, 74)),
               (QColor(35, 42, 49), QColor(35, 42, 49)),
               (QColor(85, 94, 102), QColor(70, 79, 87)))
# Cooldown numbers are rasterised by Pillow; tune the shadow with these four.
NUMBER_BOX = (88, 64)       # design units, both even so the centred blit stays crisp
NUMBER_FILL = (242, 242, 237, 255)
SHADOW_FILL, SHADOW_SPREAD, SHADOW_BLUR, SHADOW_OFFSET = (0, 0, 0, 235), 2, 2, (0, 0)
FONT_FILE = ASSETS / "radiance-regular.otf"
NUMBER_FONT = ImageFont.truetype(str(FONT_FILE) if FONT_FILE.exists() else "arialbd.ttf", 38)

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


@lru_cache(maxsize=64)
def number_image(text):
    """Rasterise one cooldown number, centred in NUMBER_BOX over a soft shadow."""
    center = (NUMBER_BOX[0] / 2, NUMBER_BOX[1] / 2)
    image = Image.new("RGBA", NUMBER_BOX)
    ImageDraw.Draw(image).text((center[0] + SHADOW_OFFSET[0], center[1] + SHADOW_OFFSET[1]),
                               text, SHADOW_FILL, NUMBER_FONT, anchor="mm",
                               stroke_width=SHADOW_SPREAD, stroke_fill=SHADOW_FILL)
    image = image.filter(ImageFilter.GaussianBlur(SHADOW_BLUR))
    ImageDraw.Draw(image).text(center, text, NUMBER_FILL, NUMBER_FONT, anchor="mm")
    return QImage(image.tobytes(), *NUMBER_BOX, QImage.Format_RGBA8888).copy()


def draw_panel(painter, rect):
    panel = QLinearGradient(0, rect.top(), 0, rect.bottom())
    panel.setColorAt(0, PANEL_TOP)
    panel.setColorAt(1, PANEL_BOTTOM)
    painter.fillRect(rect, panel)
    # Native HUD panels have a faint top lip, not a bright enclosing outline.
    painter.fillRect(QRectF(rect.left(), rect.top(), rect.width(), 2), QColor(137, 148, 151, 65))
    painter.fillRect(QRectF(rect.left(), rect.bottom() - 2, rect.width(), 2),
                     QColor(0, 0, 0, 110))


def draw_bevel(painter, outer, width, colours):
    """Fill the ring inside `outer` with one shaded edge per side, mitred at the corners.

    Mitres are drawn unantialiased: the diagonals abut exactly, and a feathered join
    would leave a hairline of panel showing through where two edges meet.
    """
    inner = outer.adjusted(width, width, -width, -width)
    edges = ((outer.topLeft(), outer.topRight(), inner.topRight(), inner.topLeft()),
             (outer.topRight(), outer.bottomRight(), inner.bottomRight(), inner.topRight()),
             (outer.bottomRight(), outer.bottomLeft(), inner.bottomLeft(), inner.bottomRight()),
             (outer.bottomLeft(), outer.topLeft(), inner.topLeft(), inner.bottomLeft()))
    painter.save()
    painter.setRenderHint(QPainter.Antialiasing, False)
    painter.setPen(Qt.NoPen)
    for points, (outer_colour, inner_colour) in zip(edges, colours):
        shade = QLinearGradient((points[0] + points[1]) / 2, (points[2] + points[3]) / 2)
        shade.setColorAt(0, outer_colour)
        shade.setColorAt(1, inner_colour)
        painter.setBrush(shade)
        painter.drawPolygon(QPolygonF(points))
    painter.restore()


def draw_spell(painter, slot, icon, left=0, fraction=0, invoked=False):
    painter.save()
    # A near-black edge, then the raised frame; the artwork butts straight onto it.
    painter.fillRect(slot, SLOT_EDGE)
    painter.fillRect(QRectF(slot.left(), slot.bottom() - EDGE, slot.width(), EDGE),
                     SLOT_EDGE_BOTTOM)
    draw_bevel(painter, slot.adjusted(EDGE, EDGE, -EDGE, -EDGE), BEVEL, BEVEL_EDGES)
    rect = slot.adjusted(FRAME, FRAME, -FRAME, -FRAME)
    painter.drawPixmap(rect, icon, QRectF(icon.rect()))
    if left > 0:
        # A clipped circular sector covers the remaining portion of the square.
        painter.fillRect(rect, QColor(0, 0, 0, 45))
        radius = math.hypot(rect.width(), rect.height()) / 2
        circle = QRectF(rect.center().x() - radius, rect.center().y() - radius,
                        2 * radius, 2 * radius)
        painter.save()
        painter.setClipRect(rect)
        painter.setPen(Qt.NoPen)
        painter.setBrush(COOLDOWN_TINT)
        painter.drawPie(circle, 90 * 16, round(360 * 16 * fraction))
        painter.restore()
        number = number_image(str(math.ceil(left)))
        painter.drawImage(rect.center() - QPointF(number.width() / 2,
                                                  number.height() / 2), number)
    if invoked:
        # Soft outer glow, then a crisp green rim replacing the slot's black edge.
        painter.setBrush(Qt.NoBrush)
        painter.setPen(QPen(QColor(111, 205, 93, 65), 2 * EDGE))
        painter.drawRect(slot.adjusted(EDGE, EDGE, -EDGE, -EDGE))
        painter.setPen(QPen(QColor(137, 221, 108, 245), EDGE))
        painter.drawRect(slot.adjusted(EDGE / 2, EDGE / 2, -EDGE / 2, -EDGE / 2))
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
            target.write_bytes(httpx.get(url, timeout=15).raise_for_status().content)

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
        painter.setRenderHints(QPainter.Antialiasing | QPainter.SmoothPixmapTransform)
        draw_panel(painter, self.design_rect)
        for spell, rect in self.spell_rects.items():
            draw_spell(painter, rect, self.icons[spell], *self.state.get(spell, (0, 0, False)))


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
