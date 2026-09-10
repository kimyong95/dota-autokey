"""Physical-pixel display bounds and overlay placement on Windows."""

import ctypes
import math

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QPainter
from PySide6.QtWidgets import QWidget
from ctypes import wintypes

user32 = ctypes.WinDLL("user32", use_last_error=True)
user32.GetSystemMetrics.argtypes = [ctypes.c_int]
user32.GetSystemMetrics.restype = ctypes.c_int
user32.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int,
                                ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.UINT]
user32.SetWindowPos.restype = wintypes.BOOL
user32.FindWindowW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR]
user32.FindWindowW.restype = wintypes.HWND
for name in ("GetWindowRect", "GetClientRect"):
    getattr(user32, name).argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
    getattr(user32, name).restype = wintypes.BOOL


def hud_surface():
    """Borderless presentation bounds and render size, which Windows may scale."""
    window = user32.FindWindowW(None, "Dota 2")
    frame, client = wintypes.RECT(), wintypes.RECT()
    if (window and user32.GetWindowRect(window, ctypes.byref(frame))
            and user32.GetClientRect(window, ctypes.byref(client))
            and client.right > 0 and client.bottom > 0):
        return ((frame.left, frame.top, frame.right - frame.left,
                 frame.bottom - frame.top), (client.right, client.bottom))
    # QApplication enables per-monitor DPI awareness, so these are physical pixels.
    width, height = user32.GetSystemMetrics(0), user32.GetSystemMetrics(1)
    return (0, 0, width, height), (width, height)


def place_overlay(window, bounds):
    # Physical positioning avoids fractional-DPI rounding. Never take keyboard focus.
    if not user32.SetWindowPos(window, wintypes.HWND(-1), *bounds, 0x0010):
        raise ctypes.WinError(ctypes.get_last_error())


class LogicalWindow(QWidget):
    """Paint in design units and anchor to the bottom-left of Dota's HUD."""

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

    def follow_bottom(self, offset, left=0):
        """Keep the window's bottom edge `offset` design units above the HUD bottom."""
        self.bottom_offset = offset
        self.left_offset = left
        if not hasattr(self, 'geometry_timer'):
            self.geometry_timer = QTimer(self)
            self.geometry_timer.timeout.connect(self.align_to_display)
            self.geometry_timer.start(250)
        self.align_to_display(force=True)

    def align_to_display(self, force=False):
        bounds, render_size = hud_surface()
        dpi = self.devicePixelRatioF()
        if not force and (bounds, render_size, dpi) == self.last_geometry:
            return
        x, y, width, height = bounds
        render_width, render_height = render_size
        # Map the height-based HUD through Windows' presentation stretch, then DPI.
        scale_y = height / self.design_height
        scale_x = render_height / self.design_height * width / render_width
        self.pixel_size = (round(self.design_rect.width() * scale_x),
                           round(self.design_rect.height() * scale_y))
        self.resize(*(math.ceil(side / dpi) for side in self.pixel_size))
        panel_width, panel_height = self.pixel_size
        place_overlay(int(self.winId()),
                      (x + round(self.left_offset * scale_x),
                       y + height - round(self.bottom_offset * scale_y) - panel_height,
                       panel_width, panel_height))
        self.last_geometry = bounds, render_size, dpi
        self.update()

    def logical_painter(self):
        """Return a painter whose coordinates are design units, with pixel-snapped edges."""
        painter = QPainter(self)
        dpi = self.devicePixelRatioF()
        painter.scale(self.pixel_size[0] / dpi / self.design_rect.width(),
                      self.pixel_size[1] / dpi / self.design_rect.height())
        return painter
