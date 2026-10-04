"""Invoker Ice Wall steering: each press of a steering key shows the Ice Wall preview and steers Invoker once
so his Ice Wall lands on the target, the virtual cursor, which every press captures since the clicks land
elsewhere. Walk takes him to WALL_DISTANCE short of the target, so the wall's centre lands on it; the parts
turn him in place so its left or right part does. Nothing happens for a target within WALL_DISTANCE, or when
he is there or facing so already. Each click is a right-click with MOVE_KEY held, Dota's directional move:
turn in place, then walk straight to the click; a Stop right after it keeps only the turn."""
import math
import threading

import keyboard
from keyboard import KEY_DOWN
from pynput.keyboard import Controller

from invoker.icewall_overlay import WALL_DISTANCE, InvokerIcewallOverlay
from invoker.virtual_cursor_overlay import VirtualCursorOverlay
from utils.camera import CameraScreenProjection
from utils.dedup_queue import DedupeQueue
from utils.window import dota_is_foreground, hud_surface

CLICKABLE = (0.02, 0.06, 0.98, 0.76)    # left, top, right, bottom fractions of the viewport above the HUD
MOVE_KEY, STOP_KEY = "m", "s"
DISTANCE_EPSILON, ANGLE_EPSILON = 50, 3     # world units past WALL_DISTANCE and degrees off the facing under which a press clicks nothing

controller = Controller()   # not keyboard.send, which stops keyboard's hooks for every key while sending


def wall_facing(hero, target, side):
    """Facing in radians that puts the Ice Wall's line through `target`, farther than WALL_DISTANCE, on its left
    (side 1) or right (-1) part."""
    dx, dy = target[0] - hero[0], target[1] - hero[1]
    return math.atan2(dy, dx) - side * math.acos(WALL_DISTANCE / math.hypot(dx, dy))


def clickable(viewport, pixel):
    left, top, width, height = viewport
    return CLICKABLE[0] <= (pixel[0] - left) / width <= CLICKABLE[2] and CLICKABLE[1] <= (pixel[1] - top) / height <= CLICKABLE[3]


class IcewallSteering:
    """keys: {key name: "walk" | "left part" | "right part"}. Create on the Qt thread.

    memory: utils.memory.MemoryReader
    """

    def __init__(self, keys, memory):
        self.memory = memory
        self.cursor_overlay = VirtualCursorOverlay()
        self.icewall_overlay = InvokerIcewallOverlay(memory)
        self.event_queue = DedupeQueue()
        for key, mode in keys.items():
            keyboard.hook_key(key, lambda event, mode=mode: self.on_key(mode, event), suppress=True)
        threading.Thread(target=self.worker, daemon=True).start()

    def on_key(self, mode, event):
        if event.event_type == KEY_DOWN and dota_is_foreground():
            self.event_queue.put(mode)

    def worker(self):
        while True:
            self.steer(self.event_queue.get())

    def steer(self, mode):
        viewport = hud_surface()[0]
        projection = CameraScreenProjection(viewport)
        self.icewall_overlay.activate()
        hero, matrix = self.memory.get_hero_position(), self.memory.get_view_matrix()
        if hero is None or matrix is None:
            return

        self.cursor_overlay.capture(viewport)
        target = projection.screen_to_world(matrix, self.cursor_overlay.position)[:2]
        distance = math.dist(target, hero[:2])
        if distance < WALL_DISTANCE + DISTANCE_EPSILON:
            return
        if mode == "walk":
            point = [t + (h - t) * WALL_DISTANCE / distance for t, h in zip(target, hero)]
        else:
            facing = wall_facing(hero, target, {"left part": 1, "right part": -1}[mode])
            if abs(math.remainder(hero[3] - math.degrees(facing), 360)) < ANGLE_EPSILON:
                return
            point = hero[0] + WALL_DISTANCE * math.cos(facing), hero[1] + WALL_DISTANCE * math.sin(facing)
        pixel = projection.world_to_screen(matrix, point)
        if clickable(viewport, pixel):
            with controller.pressed(MOVE_KEY):
                self.cursor_overlay.right_click(*pixel)
            if mode != "walk":
                controller.tap(STOP_KEY)    # stops the walk, not the turn
