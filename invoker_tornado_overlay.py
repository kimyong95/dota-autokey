"""Invoker Tornado combo timer: a three-part ring around the cursor for SHOW_SECONDS after Tornado.

After Tornado is cast, each follow-up's arc fills up until the moment to cast it so that it lands
exactly when the lifted enemy drops: Sun Strike on top, EMP bottom-left, Chaos Meteor bottom-right.
A full arc lights up (cast it now); the arc of a spell on cooldown is dimmed. The enemy is taken to
be under the cursor, so its distance from where Invoker cast Tornado, and with it the Tornado travel
time, follows the cursor (converted to the world by utils.CameraScreenProjection).

Timing, with the values of Valve's hero datafeed: Tornado leaves CAST_POINT after the release, flies
at TORNADO_SPEED, lifts what it touches (TORNADO_RADIUS) for LIFT_DURATION[Quas level]; a follow-up
lands CAST_POINT + its delay after being cast. Invoker's turn time before casting is not modelled.
"""
import math
import time
import win32api
from PySide6.QtCore import QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import QWidget
import utils
from window_utils import dota_is_foreground, hud_surface, place_overlay

CAST_POINT = 0.05
TORNADO_SPEED, TORNADO_RADIUS = 1000, 200
TORNADO_DISTANCE = [1500, 1800, 2100, 2400, 2700, 3000, 3300, 3600, 3900]     # by Wex level
LIFT_DURATION = [1.2, 1.4, 1.6, 1.8, 2.0, 2.2, 2.4, 2.6, 2.8]                 # by Quas level
FOLLOW_UPS = {          # spell: (delay before it lands, arc centre in degrees, 0 = right, counter-clockwise; colour)
    "invoker_sun_strike":   (1.7, 90, QColor(255, 215, 90)),    # yellow
    "invoker_emp":          (2.9, 210, QColor(150, 185, 255)),  # blue
    "invoker_chaos_meteor": (1.3, 330, QColor(255, 90, 40)),
}
SHOW_SECONDS = 4.0      # the ring is shown this long after each Tornado cast
RING_SIZE, RING_WIDTH, ARC_SPAN = 120, 10, 110     # physical pixels, pixels, degrees per arc
CURSOR_CENTER = (14, 24)    # Dota's 48 px arrow cursor: visible centre of the icon relative to its tip
REFRESH_MS = 16


def by_level(values, level):
    return values[min(max(level, 1), len(values)) - 1]


def follow_up_progress(cast_time, distance, quas_level, wex_level, now):
    """{spell: 0..1 of the wait until its cast moment} for a Tornado cast at `cast_time` towards an
    enemy `distance` away."""
    distance = min(distance, by_level(TORNADO_DISTANCE, wex_level))
    hit = cast_time + CAST_POINT + max(0, distance - TORNADO_RADIUS) / TORNADO_SPEED
    land = hit + by_level(LIFT_DURATION, quas_level)
    progress = {}
    for spell, (delay, _, _) in FOLLOW_UPS.items():
        wait = land - CAST_POINT - delay - cast_time
        progress[spell] = 1.0 if wait <= 0 else min(1.0, (now - cast_time) / wait)
    return progress


class InvokerTornadoOverlay(QWidget):
    """Combo ring that follows the cursor while Dota is the foreground window. Create on the Qt thread.

    get_state() -> {"cast": (release time, hero x, hero y) of the last Tornado, or None,
                    "quas_level": int, "wex_level": int, "ready": {spell: off cooldown}}
    camera: utils.Camera
    """

    def __init__(self, get_state, camera):
        super().__init__()
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint
                            | Qt.Tool | Qt.WindowTransparentForInput)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.get_state = get_state
        self.camera = camera
        self.bounds = None
        self.projection = None
        self.progress = {}      # spell -> 0..1, empty while no Tornado combo is running
        self.ready = {}
        side = math.ceil(RING_SIZE / self.devicePixelRatioF())
        self.resize(side, side)
        timer = QTimer(self)
        timer.timeout.connect(self.refresh)
        timer.start(REFRESH_MS)

    def refresh(self):
        state, camera, now = self.get_state(), self.camera.position, time.monotonic()
        if not dota_is_foreground() or not state["cast"] or now - state["cast"][0] > SHOW_SECONDS:
            self.progress = {}
            self.hide()
            return
        bounds = hud_surface()[0]
        if bounds != self.bounds:
            self.bounds, self.projection = bounds, utils.CameraScreenProjection(bounds)
        cursor = win32api.GetCursorPos()
        self.ready = state["ready"]
        self.progress = {}
        if camera:
            cast_time, hx, hy = state["cast"]
            x, y, _ = self.projection.screen_to_world(camera, cursor)
            self.progress = follow_up_progress(cast_time, math.hypot(x - hx, y - hy), state["quas_level"],
                                               state["wex_level"], now)
        left = cursor[0] + CURSOR_CENTER[0] - RING_SIZE // 2
        top = cursor[1] + CURSOR_CENTER[1] - RING_SIZE // 2
        place_overlay(int(self.winId()), (left, top, RING_SIZE, RING_SIZE))
        self.show()
        self.update()

    def is_lit(self, spell):
        """False only while the ring shows `spell`'s arc still filling. Safe from any thread."""
        return self.progress.get(spell, 1.0) >= 1.0

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.scale(1 / self.devicePixelRatioF(), 1 / self.devicePixelRatioF())
        inset = RING_WIDTH / 2 + 3
        rect = QRectF(inset, inset, RING_SIZE - 2 * inset, RING_SIZE - 2 * inset)
        for spell, (_, centre, colour) in FOLLOW_UPS.items():
            start = round((centre - ARC_SPAN / 2) * 16)                 # Qt arcs are in 1/16 degree
            ready = self.ready.get(spell, True)
            fraction = self.progress.get(spell, 0.0)
            painter.setPen(QPen(QColor(0, 0, 0, 120 if ready else 50), RING_WIDTH, Qt.SolidLine, Qt.FlatCap))
            painter.drawArc(rect, start, ARC_SPAN * 16)                 # empty track
            if fraction > 0:
                lit = fraction >= 1
                fill = QColor(colour)
                fill.setAlpha(60 if not ready else 255 if lit else 150)
                width = RING_WIDTH + (4 if lit and ready else 0)
                painter.setPen(QPen(fill, width, Qt.SolidLine, Qt.FlatCap))
                painter.drawArc(rect, start, round(fraction * ARC_SPAN * 16))
