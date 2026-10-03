"""Physical-pixel display bounds and overlay placement on Windows."""

import math

import win32api
import win32con
import win32gui
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QPainter
from PySide6.QtWidgets import QWidget


def hud_surface():
    """Dota's presented client bounds and render size; Windows may scale one to the other."""
    try:
        window = win32gui.FindWindow(None, "Dota 2")
        client = win32gui.GetClientRect(window)
        if client[2] > 0 and client[3] > 0 and not win32gui.IsIconic(window):
            left, top = win32gui.ClientToScreen(window, (0, 0))
            right, bottom = win32gui.ClientToScreen(window, (client[2], client[3]))
            return (left, top, right - left, bottom - top), (client[2], client[3])
    except win32gui.error:
        pass
    size = (win32api.GetSystemMetrics(0), win32api.GetSystemMetrics(1))
    return (0, 0, *size), size


def dota_is_foreground():
    return win32gui.GetWindowText(win32gui.GetForegroundWindow()) == "Dota 2"


def place_overlay(window, bounds):
    win32gui.SetWindowPos(window, win32con.HWND_TOPMOST, *bounds, win32con.SWP_NOACTIVATE)


class LogicalScreen:
    """Logical pixels (a 16:9 screen `design_height` tall) to screen pixels, the way Dota scales its HUD.

        screen = LogicalScreen(*hud_surface())
        screen.rect(1561, 1894, 96, 96)     # a rect measured off a 4K screenshot -> (left, top, width, height)

    The HUD scales with the render height and stays anchored to the bottom centre, so a HUD spot measured
    on a 4K screenshot lands on the same spot at any resolution or aspect ratio. Screen pixels are physical
    only in a DPI-aware process: Qt makes its process aware; others must call SetProcessDpiAwarenessContext.
    """

    def __init__(self, bounds, render_size, design_height=2160):
        self.bounds, self.render_size = bounds, render_size
        self.design_height = design_height
        x, y, width, height = bounds
        render_width, render_height = render_size
        self.scale_y = height / design_height
        self.scale_x = render_height / design_height * width / render_width

    def point(self, x, y):
        """Screen position of a logical point, unrounded."""
        left, top, width, height = self.bounds
        return (left + width / 2 + (x - self.design_height * 8 / 9) * self.scale_x,
                top + height + (y - self.design_height) * self.scale_y)

    def rect(self, x, y, width, height):
        """Screen pixel rect (left, top, width, height) of a logical rect; edges round, so neighbours tile."""
        left, top = map(round, self.point(x, y))
        right, bottom = map(round, self.point(x + width, y + height))
        return left, top, right - left, bottom - top


class LogicalWindow(QWidget):
    """Paint in design units and anchor to Dota's HUD ability row."""

    def __init__(self, design_rect, design_height=2160):
        super().__init__()
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint
                            | Qt.Tool | Qt.WindowTransparentForInput)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.design_rect = design_rect
        self.design_height = design_height
        self.pixel_size = (round(design_rect.width()), round(design_rect.height()))
        self.last_geometry = None
        self.resize(*self.pixel_size)

    def follow_hud(self, bottom_offset, center_offset=0):
        """Bottom edge `bottom_offset` design units above the HUD bottom, centred less `center_offset`."""
        self.bottom_offset = bottom_offset
        self.center_offset = center_offset
        if not hasattr(self, 'geometry_timer'):
            self.geometry_timer = QTimer(self)
            self.geometry_timer.timeout.connect(self.align_to_display)
            self.geometry_timer.start(250)
        self.align_to_display(force=True)

    def align_to_display(self, force=False):
        screen = LogicalScreen(*hud_surface(), self.design_height)
        dpi = self.devicePixelRatioF()
        if not force and (screen.bounds, screen.render_size, dpi) == self.last_geometry:
            return
        x, y, width, height = screen.bounds
        self.pixel_size = (round(self.design_rect.width() * screen.scale_x),
                           round(self.design_rect.height() * screen.scale_y))
        self.resize(*(math.ceil(side / dpi) for side in self.pixel_size))
        panel_width, panel_height = self.pixel_size
        place_overlay(int(self.winId()),
                      (x + round((width - panel_width) / 2) - round(self.center_offset * screen.scale_x),
                       y + height - round(self.bottom_offset * screen.scale_y) - panel_height,
                       panel_width, panel_height))
        self.last_geometry = screen.bounds, screen.render_size, dpi
        self.update()

    def logical_painter(self):
        painter = QPainter(self)
        dpi = self.devicePixelRatioF()
        painter.scale(self.pixel_size[0] / dpi / self.design_rect.width(),
                      self.pixel_size[1] / dpi / self.design_rect.height())
        return painter
