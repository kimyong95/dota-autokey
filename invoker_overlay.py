"""Invoker panel layout, painting, refresh, and hold-alt visibility."""
import math
import signal
from functools import lru_cache
from pathlib import Path
import httpx
import keyboard
from keyboard import KEY_DOWN
from PIL import Image, ImageDraw, ImageFilter, ImageFont
from PySide6.QtCore import QPointF, QRectF, Qt, QTimer, Signal, Slot
from PySide6.QtGui import (QColor, QImage, QLinearGradient, QPainter, QPen,
                           QPixmap, QPolygonF)
from PySide6.QtWidgets import QApplication
from window_utils import LogicalWindow

ASSETS = Path(__file__).parent / "assets"
# 4K design units, measured off Dota's own HUD so the six columns sit squarely
# over the six ability slots and the panel reads as the HUD bar carried upwards.
SIZE, PAD, EDGE, BEVEL = 92, 8, 4, 4
FRAME = EDGE + BEVEL
SLOT = SIZE + 2 * FRAME
COLUMNS, ROWS = 6, 2
PANEL_WIDTH = COLUMNS * SLOT + (COLUMNS + 1) * PAD
PANEL_HEIGHT = ROWS * SLOT + (ROWS + 1) * PAD
HUD_BAR_TOP, ABILITY_ROW_SHIFT = 526, 45   # bar lip above HUD bottom; row centre left of screen centre
LIP, LIP_FADE = 2, 6
OVERLAY_OPACITY = 1.0
DIVIDER_AFTER_COLUMN, DIVIDER_WIDTH, DIVIDER_INSET = 4, 2, PAD   # splits the panel into two groups
DIVIDER = QColor(255, 255, 255)
PANEL_TOP = QColor(68, 77, 86, 232)
PANEL_BOTTOM = QColor(62, 71, 80, 232)
PANEL_LIP = QColor(96, 101, 111, 232)
PANEL_SIDE = QColor(24, 29, 34, 150)
COOLDOWN_TINT = QColor(0, 0, 0, 185)
SLOT_EDGE = QColor(23, 27, 30, 240)
SLOT_EDGE_BOTTOM = QColor(15, 18, 20, 240)
BEVEL_EDGES = ((QColor(138, 146, 153), QColor(107, 116, 124)),    # clockwise from the top
               (QColor(43, 51, 59), QColor(58, 66, 74)),
               (QColor(35, 42, 49), QColor(35, 42, 49)),
               (QColor(85, 94, 102), QColor(70, 79, 87)))
NUMBER_BOX = (112, 82)
NUMBER_FILL = (242, 242, 237, 255)
SHADOW_FILL, SHADOW_SPREAD, SHADOW_BLUR, SHADOW_OFFSET = (0, 0, 0, 235), 2, 2, (0, 0)
FONT_FILE = ASSETS / "radiance-regular.otf"
NUMBER_FONT = ImageFont.truetype(str(FONT_FILE) if FONT_FILE.exists() else "arialbd.ttf", 48)

# Autokey trigger keys in panel order:
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
}


@lru_cache(maxsize=64)
def number_image(text):
    center = (NUMBER_BOX[0] / 2, NUMBER_BOX[1] / 2)
    image = Image.new("RGBA", NUMBER_BOX)
    ImageDraw.Draw(image).text((center[0] + SHADOW_OFFSET[0], center[1] + SHADOW_OFFSET[1]),
                               text, SHADOW_FILL, NUMBER_FONT, anchor="mm",
                               stroke_width=SHADOW_SPREAD, stroke_fill=SHADOW_FILL)
    image = image.filter(ImageFilter.GaussianBlur(SHADOW_BLUR))
    ImageDraw.Draw(image).text(center, text, NUMBER_FILL, NUMBER_FONT, anchor="mm")
    return QImage(image.tobytes(), *NUMBER_BOX, QImage.Format_RGBA8888).copy()


def draw_panel(painter, rect):
    body = QLinearGradient(0, rect.top(), 0, rect.bottom())
    body.setColorAt(0, PANEL_TOP)
    body.setColorAt(1, PANEL_BOTTOM)
    painter.fillRect(rect, body)
    lip = QLinearGradient(0, rect.top(), 0, rect.top() + LIP_FADE)
    lip.setColorAt(0, PANEL_LIP)
    lip.setColorAt(LIP / LIP_FADE, PANEL_LIP)
    lip.setColorAt(1, QColor(PANEL_LIP.red(), PANEL_LIP.green(), PANEL_LIP.blue(), 0))
    painter.fillRect(QRectF(rect.left(), rect.top(), rect.width(), LIP_FADE), lip)
    for x in (rect.left(), rect.right() - LIP):
        painter.fillRect(QRectF(x, rect.top(), LIP, rect.height()), PANEL_SIDE)
    # centred in the gap after DIVIDER_AFTER_COLUMN
    x = rect.left() + DIVIDER_AFTER_COLUMN * (SLOT + PAD) + (PAD - DIVIDER_WIDTH) / 2
    painter.fillRect(QRectF(x, rect.top() + DIVIDER_INSET, DIVIDER_WIDTH,
                            rect.height() - 2 * DIVIDER_INSET), DIVIDER)


def draw_bevel(painter, outer, width, colours):
    inner = outer.adjusted(width, width, -width, -width)
    edges = ((outer.topLeft(), outer.topRight(), inner.topRight(), inner.topLeft()),
             (outer.topRight(), outer.bottomRight(), inner.bottomRight(), inner.topRight()),
             (outer.bottomRight(), outer.bottomLeft(), inner.bottomLeft(), inner.bottomRight()),
             (outer.bottomLeft(), outer.topLeft(), inner.topLeft(), inner.bottomLeft()))
    painter.save()
    painter.setRenderHint(QPainter.Antialiasing, False)   # feathered mitres leak the panel through
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
    painter.fillRect(slot, SLOT_EDGE)
    painter.fillRect(QRectF(slot.left(), slot.bottom() - EDGE, slot.width(), EDGE),
                     SLOT_EDGE_BOTTOM)
    draw_bevel(painter, slot.adjusted(EDGE, EDGE, -EDGE, -EDGE), BEVEL, BEVEL_EDGES)
    rect = slot.adjusted(FRAME, FRAME, -FRAME, -FRAME)
    painter.drawPixmap(rect, icon, QRectF(icon.rect()))
    if left > 0:
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
        painter.setBrush(Qt.NoBrush)
        painter.setPen(QPen(QColor(111, 205, 93, 65), 2 * EDGE))
        painter.drawRect(slot.adjusted(EDGE, EDGE, -EDGE, -EDGE))
        painter.setPen(QPen(QColor(137, 221, 108, 245), EDGE))
        painter.drawRect(slot.adjusted(EDGE / 2, EDGE / 2, -EDGE / 2, -EDGE / 2))
    painter.restore()


class Overlay(LogicalWindow):
    show_requested = Signal(bool)

    def __init__(self, get_state):
        step = SLOT + PAD
        super().__init__(QRectF(0, 0, PANEL_WIDTH, PANEL_HEIGHT))
        self.setWindowOpacity(OVERLAY_OPACITY)
        self.get_state = get_state
        self.state = {}
        self.spell_rects = {
            spell: QRectF(PAD + col * step, PAD + row * step, SLOT, SLOT)
            for spell, (col, row) in LAYOUT.items()
        }
        self.prepare_assets()
        self.icons = {spell: QPixmap(str(ASSETS / f"{spell}.png")) for spell in LAYOUT}
        self.show_requested.connect(self.set_shown, Qt.QueuedConnection)
        self.follow_hud(HUD_BAR_TOP - LIP, ABILITY_ROW_SHIFT)
        timer = QTimer(self)
        timer.timeout.connect(self.refresh)
        timer.start(50)
        self.refresh()

    def prepare_assets(self):
        ASSETS.mkdir(parents=True, exist_ok=True)
        for spell in LAYOUT:
            target = ASSETS / f"{spell}.png"
            if target.exists():
                continue
            url = f"https://cdn.cloudflare.steamstatic.com/apps/dota2/images/dota_react/abilities/{spell}.png"
            target.write_bytes(httpx.get(url, timeout=15).raise_for_status().content)

    @Slot(bool)
    def set_shown(self, shown):
        if shown and not self.isVisible():
            self.align_to_display(force=True)
        self.setVisible(shown)

    def refresh(self):
        self.set_state(self.get_state())

    def set_state(self, state):
        """state: {spell: (seconds_remaining, cooldown_fraction, invoked)}"""
        self.state = state
        self.update()

    def paintEvent(self, event):
        painter = self.logical_painter()
        painter.setRenderHints(QPainter.Antialiasing | QPainter.SmoothPixmapTransform)
        draw_panel(painter, self.design_rect)
        for spell, rect in self.spell_rects.items():
            draw_spell(painter, rect, self.icons[spell], *self.state.get(spell, (0, 0, False)))


def start(get_state):
    """Blocks in the Qt event loop; must run on the main thread."""
    qt = QApplication([])
    qt.setQuitOnLastWindowClosed(False)
    overlay = Overlay(get_state)
    signal.signal(signal.SIGINT, lambda *_: qt.quit())

    # panel is shown only while alt is held; the hook must not block, so hand
    # off to the Qt thread through the queued signal
    hook = keyboard.hook_key("alt", lambda event: overlay.show_requested.emit(event.event_type == KEY_DOWN))
    try:
        qt.exec()
    finally:
        keyboard.unhook(hook)