import math
import re
import threading
import time
from pathlib import Path

import keyboard
import numpy as np

from install_configs import find_dota
from window_utils import dota_is_foreground


class CameraScreenProjection:
    """Screen pixel <-> world point on Dota's terrain, for the default camera.

        projection = utils.CameraScreenProjection(viewport)
        x, y, z = projection.screen_to_world(camera, (sx, sy))
        sx, sy = projection.world_to_screen(camera, (x, y))

    camera is the eye position (x, y, z) from `dota_camera_get_pos` (see Camera). viewport is
    (left, top, width, height) of the full 3D render, including behind the HUD; screen pixels are
    in the same space, origin top-left, y down. The camera looks along world +Y, pitched
    PITCH_DEGREES down, with a VERTICAL_FOV_DEGREES field of view and no roll. World z always
    comes from the measured terrain (data/map-height.npy from measure-map-data.py).
    """

    PITCH_DEGREES = 60.0
    VERTICAL_FOV_DEGREES = 66.1
    MAP_MIN = -16352        # world x / y of index 0 in map-height.npy (GetWorldMinX / GetWorldMinY)

    def __init__(self, viewport):
        left, top, width, height = viewport
        pitch = math.radians(self.PITCH_DEGREES)
        self.forward = (0.0, math.cos(pitch), -math.sin(pitch))
        self.up = (0.0, math.sin(pitch), math.cos(pitch))
        self.focal = height / (2 * math.tan(math.radians(self.VERTICAL_FOV_DEGREES) / 2))
        self.center = (left + width / 2, top + height / 2)
        self.heights = np.load(Path(__file__).with_name("data") / "map-height.npy", mmap_mode="r")

    def height(self, x, y):
        """Terrain height at world (x, y)."""
        return int(self.heights[round(x) - self.MAP_MIN, round(y) - self.MAP_MIN])

    def world_to_screen(self, camera, world):
        """Screen pixel of the terrain point at world (x, y)."""
        x, y = world[:2]
        relative = (x - camera[0], y - camera[1], self.height(x, y) - camera[2])
        depth = sum(r * f for r, f in zip(relative, self.forward))
        vertical = sum(r * u for r, u in zip(relative, self.up))
        return self.center[0] + self.focal * relative[0] / depth, self.center[1] - self.focal * vertical / depth

    def screen_to_world(self, camera, screen):
        """Terrain point (x, y, z) under a screen pixel.

        Intersects the pixel's ray with the plane at a guessed height, then again at the
        terrain height found there; a few rounds settle on the terrain.
        """
        horizontal = (screen[0] - self.center[0]) / self.focal
        vertical = (self.center[1] - screen[1]) / self.focal
        ray = (horizontal, self.forward[1] + vertical * self.up[1], self.forward[2] + vertical * self.up[2])
        z = self.height(camera[0], camera[1])
        for _ in range(4):
            distance = (z - camera[2]) / ray[2]
            x, y = camera[0] + distance * ray[0], camera[1] + distance * ray[1]
            z = self.height(x, y)
        return x, y, z


class Camera:
    """The camera eye position, kept fresh by a background thread.

        camera = utils.Camera()
        camera.position          # (x, y, z), or None until Dota first answers

    Every `interval` seconds: presses `key` if Dota is the foreground window (autoexec.cfg binds
    it to `dota_camera_get_pos`, which -condebug logs to console.log), then reads the last
    TAIL_BYTES of console.log and takes the latest complete camera line written there.
    """

    LINE_RE = re.compile(rb"Camera position:\s+(-?[\d.]+)\s+(-?[\d.]+)\s+(-?[\d.]+)")
    TAIL_BYTES = 2048       # one F10 round logs ~150 bytes; 99 % of gaps between camera lines < 900

    def __init__(self, interval=0.05, key="f10"):
        self.position = None
        self.log = find_dota() / "game" / "dota" / "console.log"
        if not self.log.exists():
            raise FileNotFoundError(f"{self.log} does not exist: is -condebug in Dota's launch options?")
        threading.Thread(target=self._run, args=(interval, key), daemon=True).start()

    def _run(self, interval, key):
        with self.log.open("rb") as f:
            start = f.seek(0, 2)                        # ignore camera lines from before we started
            while True:
                if dota_is_foreground():
                    keyboard.press_and_release(key)
                time.sleep(interval)
                end = f.seek(0, 2)
                f.seek(max(start, end - self.TAIL_BYTES))
                tail = f.read(end - f.tell())
                tail = tail[:tail.rfind(b"\n") + 1]     # drop a line Dota is still writing
                if matches := self.LINE_RE.findall(tail):
                    self.position = tuple(map(float, matches[-1]))    # the latest one written


def updated_abilities(curr_abilities, prev_abilities, filter_info):
    """Current info of the abilities whose `filter_info` field changed this tick.

    GSI's `previously.abilities` lists only the fields that changed, so a slot
    appearing there with `filter_info` in it is one that just ticked. The value
    returned is the *current* info for that slot, keyed by slot.
    """
    if not isinstance(prev_abilities, dict):    # GSI sends `false` when the block is new
        return {}
    return {
        prev_ability_slot: curr_abilities[prev_ability_slot]
        for prev_ability_slot, prev_ability_info in prev_abilities.items()
        if filter_info in prev_ability_info
    }
