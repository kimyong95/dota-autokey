"""Invoker Ice Wall preview: where the wall lands for the current facing, drawn on the terrain.

The wall is the line WALL_DISTANCE in front of Invoker and perpendicular to his facing, sampled
every SAMPLE_STEP world units; each sample is projected with utils.CameraScreenProjection, which puts
it on the measured terrain, so the drawn polyline follows slopes and cliffs. The camera position comes
from utils.Camera (F10 -> console.log, needs -condebug).
"""
import math
from PySide6.QtCore import QPointF, Qt, QTimer, Signal, Slot
from PySide6.QtGui import QColor, QPainter, QPen, QPolygonF
from PySide6.QtWidgets import QWidget
import utils
from window_utils import hud_surface, place_overlay

WALL_DISTANCE, WALL_LENGTH = 200, 1200
SAMPLE_STEP = 50                # world units between projected wall points
REFRESH_MS = 16


def wall_points(x, y, yaw):
    """World (x, y) samples along the Ice Wall of a hero at (x, y) facing `yaw` degrees."""
    fx, fy = math.cos(math.radians(yaw)), math.sin(math.radians(yaw))
    cx, cy = x + WALL_DISTANCE * fx, y + WALL_DISTANCE * fy
    offsets = range(-WALL_LENGTH // 2, WALL_LENGTH // 2 + 1, SAMPLE_STEP)
    return [(cx - fy * s, cy + fx * s) for s in offsets]


class InvokerIcewallOverlay(QWidget):
    """Ice Wall line on the ground, shown on request. Create on the Qt thread.

    get_state() -> the hero's (x, y, yaw) with yaw in degrees (0 = +x, 90 = +y), or None
    """
    show_requested = Signal(bool)

    def __init__(self, get_state):
        super().__init__()
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint
                            | Qt.Tool | Qt.WindowTransparentForInput)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.get_state = get_state
        self.camera = utils.Camera()
        self.projection = None  # set while shown, for the viewport at that time
        self.origin = QPointF()
        self.points = []
        self.show_requested.connect(self.set_shown, Qt.QueuedConnection)
        timer = QTimer(self)
        timer.timeout.connect(self.refresh)
        timer.start(REFRESH_MS)

    @Slot(bool)
    def set_shown(self, shown):
        if shown and self.projection is None:
            bounds = hud_surface()[0]
            self.projection = utils.CameraScreenProjection(bounds)
            self.origin = QPointF(*bounds[:2])
            self.resize(*(math.ceil(v / self.devicePixelRatioF()) for v in bounds[2:]))
            place_overlay(int(self.winId()), bounds)
        elif not shown:
            self.projection = None
            self.hide()

    def refresh(self):
        hero, camera = self.get_state(), self.camera.position
        if self.projection is None or hero is None or camera is None:
            return
        self.points = [QPointF(*self.projection.world_to_screen(camera, p)) - self.origin
                       for p in wall_points(*hero)]
        self.show()
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.scale(1 / self.devicePixelRatioF(), 1 / self.devicePixelRatioF())
        line = QPolygonF(self.points)
        painter.setPen(QPen(QColor(70, 255, 80, 80), 16, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        painter.drawPolyline(line)
        painter.setPen(QPen(QColor(90, 255, 100, 200), 3))
        painter.drawPolyline(line)
