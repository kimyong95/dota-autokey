"""Invoker Ice Wall preview: the wall for the current facing, drawn on the terrain."""
import math
import time
from PySide6.QtCore import QPointF, Qt, QTimer
from PySide6.QtGui import QColor, QPainter, QPen, QPolygonF
from PySide6.QtWidgets import QWidget
from utils.camera import CameraScreenProjection
from utils.window import hud_surface, place_overlay

WALL_DISTANCE, WALL_LENGTH = 200, 1200
SAMPLE_STEP = 50
SHOW_SECONDS, FADE_SECONDS = 3, 1
REFRESH_MS = 16


def wall_points(x, y, yaw):
    fx, fy = math.cos(math.radians(yaw)), math.sin(math.radians(yaw))
    cx, cy = x + WALL_DISTANCE * fx, y + WALL_DISTANCE * fy
    offsets = range(-WALL_LENGTH // 2, WALL_LENGTH // 2 + 1, SAMPLE_STEP)
    return [(cx - fy * s, cy + fx * s) for s in offsets]


class InvokerIcewallOverlay(QWidget):
    """Shown for SHOW_SECONDS after each activate(), then fades out over FADE_SECONDS. Create on the Qt thread.

    memory: utils.memory.MemoryReader
    """

    def __init__(self, memory):
        super().__init__()
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint
                            | Qt.Tool | Qt.WindowTransparentForInput)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.memory = memory
        self.activated_at = -math.inf
        self.projection = None
        self.origin = QPointF()
        self.points = []
        self.opacity = 1.0
        timer = QTimer(self)
        timer.timeout.connect(self.refresh)
        timer.start(REFRESH_MS)

    def activate(self):
        self.activated_at = time.monotonic()

    def refresh(self):
        shown_for = time.monotonic() - self.activated_at
        if shown_for > SHOW_SECONDS + FADE_SECONDS:
            self.projection = None
            self.hide()
            return
        if self.projection is None:
            bounds = hud_surface()[0]
            self.projection = CameraScreenProjection(bounds)
            self.origin = QPointF(*bounds[:2])
            self.resize(*(math.ceil(v / self.devicePixelRatioF()) for v in bounds[2:]))
            place_overlay(int(self.winId()), bounds)
        hero, matrix = self.memory.get_hero_position(), self.memory.get_view_matrix()
        if hero is None or matrix is None:
            return
        x, y, _, yaw = hero
        self.points = [QPointF(*self.projection.world_to_screen(matrix, p)) - self.origin
                       for p in wall_points(x, y, yaw)]
        self.opacity = min(1.0, (SHOW_SECONDS + FADE_SECONDS - shown_for) / FADE_SECONDS)
        self.show()
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setOpacity(self.opacity)
        painter.scale(1 / self.devicePixelRatioF(), 1 / self.devicePixelRatioF())
        line = QPolygonF(self.points)
        painter.setPen(QPen(QColor(70, 255, 80, 80), 16, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        painter.drawPolyline(line)
        painter.setPen(QPen(QColor(90, 255, 100, 200), 3))
        painter.drawPolyline(line)
