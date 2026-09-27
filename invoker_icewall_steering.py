"""Invoker Ice Wall steering: each press of a steering key shows the Ice Wall preview and steers Invoker once
so his Ice Wall lands on the target, the cursor. A turn clicks elsewhere, so it first captures the virtual
cursor to hold the target; while captured, that is the target for walking too. MOVE_KEY is held while a
steering key is, so every right-click is Dota's directional move: turn in place, then walk straight."""
import math
import threading
import time

import keyboard
import win32api
from keyboard import KEY_DOWN
from pynput.keyboard import Controller

import utils
from invoker_icewall_overlay import WALL_DISTANCE, InvokerIcewallOverlay
from virtual_cursor_overlay import VirtualCursorOverlay
from window_utils import dota_is_foreground, hud_surface

CLICKABLE = (0.02, 0.06, 0.98, 0.76)    # left, top, right, bottom fractions of the viewport above the HUD
MOVE_KEY, STOP_KEY = "m", "s"

controller = Controller()   # not keyboard.send, which stops keyboard's hooks for every key while sending


def wall_facing(hero, target, side):
    """Facing in degrees that puts the Ice Wall's line through `target`, on its left (side 1) or right (-1) part."""
    dx, dy = target[0] - hero[0], target[1] - hero[1]
    distance = math.hypot(dx, dy) or 1
    return math.degrees(math.atan2(dy, dx) - side * math.acos(min(1, WALL_DISTANCE / distance)))


def clickable(viewport, pixel):
    left, top, width, height = viewport
    return CLICKABLE[0] <= (pixel[0] - left) / width <= CLICKABLE[2] and CLICKABLE[1] <= (pixel[1] - top) / height <= CLICKABLE[3]


class IcewallSteering:
    """keys: {key name: "walk" | "left part" | "right part"}. Create on the Qt thread."""

    def __init__(self, keys, hero_position, camera):
        self.hero_position = hero_position
        self.camera = camera
        self.cursor_overlay = VirtualCursorOverlay()
        self.icewall_overlay = InvokerIcewallOverlay(lambda: hero_position.forecast(time.monotonic()), camera)
        self.event_queue = utils.DedupeQueue()
        for key, mode in keys.items():
            keyboard.hook_key(key, lambda event, mode=mode: self.on_key(mode, event), suppress=True)
        threading.Thread(target=self.worker, daemon=True).start()

    def on_key(self, mode, event):
        if event.event_type != KEY_DOWN:
            controller.release(MOVE_KEY)
        elif dota_is_foreground():
            controller.press(MOVE_KEY)
            self.event_queue.put(mode)

    def worker(self):
        while True:
            self.steer(self.event_queue.get())

    def steer(self, mode):
        viewport = hud_surface()[0]
        projection = utils.CameraScreenProjection(viewport)
        self.icewall_overlay.activate()
        hero, eye = self.hero_position.get(), self.camera.position
        if hero is None or eye is None:
            return

        if mode == "walk":
            cursor = win32api.GetCursorPos()
            target = projection.screen_to_world(eye, cursor)[:2]
            if clickable(viewport, cursor):
                VirtualCursorOverlay.right_click(*cursor)
            if math.dist(target, hero[:2]) < WALL_DISTANCE + 50:
                controller.tap(STOP_KEY)    # stops the walk, not the turn

        else:
            self.cursor_overlay.capture(viewport)
            target = projection.screen_to_world(eye, self.cursor_overlay.position)[:2]
            facing = math.radians(wall_facing(hero, target, {"left part": 1, "right part": -1}[mode]))
            wall_centre = hero[0] + WALL_DISTANCE * math.cos(facing), hero[1] + WALL_DISTANCE * math.sin(facing)
            pixel = projection.world_to_screen(eye, wall_centre)
            if clickable(viewport, pixel):
                VirtualCursorOverlay.right_click(*pixel)
            controller.tap(STOP_KEY)        # stops the walk, not the turn
