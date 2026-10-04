"""Virtual cursor: while captured, the mouse moves a drawn copy of the system cursor, which is hidden and the
program's to click with; a real right-click releases it."""
import ctypes
import math
import threading
import time
from ctypes import wintypes

import win32api
import win32con
import win32gui
import win32ui
from PySide6.QtCore import Qt, QTimer, Signal, Slot
from PySide6.QtGui import QImage, QPainter
from PySide6.QtWidgets import QWidget

from utils.window import place_overlay

REFRESH_MS = 16
CLICK_GAP = 0.01

user32 = ctypes.WinDLL("user32", use_last_error=True)
magnification = ctypes.WinDLL("Magnification")     # MagShowSystemCursor hides any cursor, Dota's own too


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [("dx", wintypes.LONG), ("dy", wintypes.LONG), ("mouseData", wintypes.DWORD),
                ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.c_size_t)]


class INPUT(ctypes.Structure):
    _fields_ = [("type", wintypes.DWORD), ("mi", MOUSEINPUT)]   # 40 bytes; any padding makes SendInput fail


class MSLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [("pt", wintypes.POINT), ("mouseData", wintypes.DWORD), ("flags", wintypes.DWORD),
                ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.c_size_t)]


HOOKPROC = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM)
user32.SetWindowsHookExW.argtypes = [ctypes.c_int, HOOKPROC, wintypes.HINSTANCE, wintypes.DWORD]
user32.SetWindowsHookExW.restype = wintypes.HHOOK
user32.CallNextHookEx.argtypes = [wintypes.HHOOK, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM]   # else OverflowError
user32.CallNextHookEx.restype = ctypes.c_ssize_t


class VirtualCursorOverlay(QWidget):
    """Create on the Qt thread; call capture(bounds) from another one. The Magnification calls all run on the
    Qt thread, since that API wants one thread."""
    capture_requested = Signal(tuple)
    release_requested = Signal()

    def __init__(self):
        super().__init__()
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint
                            | Qt.Tool | Qt.WindowTransparentForInput)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.active = False
        self.position = (0, 0)
        self.look = None
        self.bounds = (0, 0, 0, 0)
        from humanmouse import HumanMouseController     # not at the top: pyautogui's import sets the process DPI awareness before Qt can
        self.human_mouse_controller = HumanMouseController(speed_factor=100)
        self.capture_requested.connect(self._capture, Qt.BlockingQueuedConnection)
        self.release_requested.connect(self._release, Qt.QueuedConnection)
        threading.Thread(target=self._hook, daemon=True).start()
        timer = QTimer(self)
        timer.timeout.connect(self.refresh)
        timer.start(REFRESH_MS)

    def capture(self, bounds):
        self.capture_requested.emit(bounds)

    @Slot(tuple)
    def _capture(self, bounds):
        if self.active:
            return
        self.look = self.cursor_look()
        self.bounds = bounds
        self.position = self._clamp(*win32api.GetCursorPos())
        self.active = True
        magnification.MagInitialize()
        magnification.MagShowSystemCursor(False)

    @Slot()
    def _release(self):
        self.move_system_cursor(*self.position)
        magnification.MagShowSystemCursor(True)
        magnification.MagUninitialize()

    def refresh(self):
        if not self.active:
            self.hide()
            return
        width, height, _, (hot_x, hot_y) = self.look
        self.resize(math.ceil(width / self.devicePixelRatioF()), math.ceil(height / self.devicePixelRatioF()))
        place_overlay(int(self.winId()), (self.position[0] - hot_x, self.position[1] - hot_y, width, height))
        self.show()
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.scale(1 / self.devicePixelRatioF(), 1 / self.devicePixelRatioF())
        width, height, pixels, _ = self.look
        painter.drawImage(0, 0, QImage(pixels, width, height, QImage.Format_ARGB32))

    @staticmethod
    def cursor_look():
        """(width, height, BGRA pixels, hotspot) of the system cursor, which must be 32-bit colour like Dota's."""
        _, handle, _ = win32gui.GetCursorInfo()
        _, hot_x, hot_y, mask, colour = win32gui.GetIconInfo(handle)
        bitmap = win32ui.CreateBitmapFromHandle(colour)
        info = bitmap.GetInfo()
        assert info["bmBitsPixel"] == 32, "the cursor is not a 32-bit colour one"
        pixels = bitmap.GetBitmapBits(True)
        win32gui.DeleteObject(mask)
        win32gui.DeleteObject(colour)
        return info["bmWidth"], info["bmHeight"], pixels, (hot_x, hot_y)

    def move_system_cursor(self, x, y):
        self.human_mouse_controller.move_to((x, y))     # glides from where the system cursor is

    def right_click(self, x, y):
        self.move_system_cursor(x, y)
        time.sleep(CLICK_GAP)
        VirtualCursorOverlay.send_mouse_input(win32con.MOUSEEVENTF_RIGHTDOWN)
        time.sleep(CLICK_GAP)
        VirtualCursorOverlay.send_mouse_input(win32con.MOUSEEVENTF_RIGHTUP)

    @staticmethod
    def send_mouse_input(flags):
        event = INPUT(type=0, mi=MOUSEINPUT(0, 0, 0, flags, 0, 0))
        if user32.SendInput(1, ctypes.byref(event), ctypes.sizeof(INPUT)) != 1:
            raise ctypes.WinError(ctypes.get_last_error())

    def _clamp(self, x, y):
        left, top, width, height = self.bounds
        return min(max(x, left), left + width - 1), min(max(y, top), top + height - 1)

    def _hook(self):
        def swallow(code, wparam, lparam):
            if code >= 0 and self.active:
                event = ctypes.cast(lparam, ctypes.POINTER(MSLLHOOKSTRUCT)).contents
                if not event.flags & win32con.LLMHF_INJECTED:     # the user's; programs' input, ours too, passes
                    if wparam == win32con.WM_MOUSEMOVE:
                        x, y = win32api.GetCursorPos()     # the hook runs before the system cursor moves
                        self.position = self._clamp(self.position[0] + event.pt.x - x, self.position[1] + event.pt.y - y)
                    elif wparam == win32con.WM_RBUTTONDOWN:
                        self.active = False
                        self.release_requested.emit()
                    return 1
            return user32.CallNextHookEx(None, code, wparam, lparam)

        self._swallow = HOOKPROC(swallow)       # kept alive as long as the hook
        if not user32.SetWindowsHookExW(win32con.WH_MOUSE_LL, self._swallow, None, 0):
            raise ctypes.WinError(ctypes.get_last_error())
        message = wintypes.MSG()
        while user32.GetMessageW(ctypes.byref(message), None, 0, 0) > 0:     # the hook is called in here
            pass
