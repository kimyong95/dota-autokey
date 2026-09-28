import math
import re
import threading
import time
from pathlib import Path

import numpy as np
import yaml
from pynput.keyboard import Controller, Key

from install_configs import find_dota
from window_utils import dota_is_foreground

controller = Controller()   # not keyboard.send, which stops keyboard's hooks for every key while sending


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
        camera.position          # (x, y, z), or None until a camera line is found

    Every `interval` seconds: presses `key` if Dota is the foreground window (autoexec.cfg binds
    it to `dota_camera_get_pos`, which -condebug logs to console.log), then reads the last
    TAIL_BYTES of console.log and takes the latest complete camera line written there.
    """

    LINE_RE = re.compile(rb"Camera position:\s+(-?[\d.]+)\s+(-?[\d.]+)\s+(-?[\d.]+)")
    TAIL_BYTES = 2048       # one F10 round logs ~150 bytes; 99 % of gaps between camera lines < 900

    def __init__(self, interval=0.05, key=Key.f10):
        self.position = None
        self.log = find_dota() / "game" / "dota" / "console.log"
        if not self.log.exists():
            raise FileNotFoundError(f"{self.log} does not exist: is -condebug in Dota's launch options?")
        threading.Thread(target=self._run, args=(interval, key), daemon=True).start()

    def _run(self, interval, key):
        with self.log.open("rb") as f:
            while True:
                if dota_is_foreground():
                    controller.tap(key)
                time.sleep(interval)
                f.seek(max(0, f.seek(0, 2) - self.TAIL_BYTES))     # Dota empties the log at each launch
                tail = f.read()
                tail = tail[:tail.rfind(b"\n") + 1]     # drop a line Dota is still writing
                if matches := self.LINE_RE.findall(tail):
                    self.position = tuple(map(float, matches[-1]))    # the latest one written


class HeroPosition:
    """The hero's (x, y, yaw) from GSI: as last reported, or forecast between posts so overlays move smoothly.

        hero_position = utils.HeroPosition()
        hero_position.update(time.monotonic(), x, y, yaw)     # on each GSI post
        hero_position.get()                                   # as last reported; for decisions
        hero_position.forecast(time.monotonic())              # extrapolated to then; for drawing only
                                                              # both None before the first post

    The forecast: position keeps the velocity between the last two posts, which is zero once the hero stops.
    Facing keeps turning at the rate between the last two posts, at most TURN_RATE, and while the hero moves
    not past his direction of travel: Dota moves him in the new direction at once and turns his facing towards
    it. yaw is in degrees, 0 = +x, 90 = +y. Measured with record-hero-pos.py. Teleports are not handled.
    """

    TURN_RATE = 660     # degrees per second, Invoker's; items do not change it

    def __init__(self):
        self.last_timestep = None
        self.last_position = None               # (x, y, yaw)
        self.last_velocity = (0.0, 0.0, 0.0)    # (x, y, yaw) per second, between the last two posts

    def update(self, now, x, y, yaw):
        velocity = (0.0, 0.0, 0.0)
        if self.last_position:
            last_x, last_y, last_yaw = self.last_position
            elapsed = now - self.last_timestep
            turned = (yaw - last_yaw + 180) % 360 - 180     # in -180..180
            velocity = ((x - last_x) / elapsed, (y - last_y) / elapsed,
                        np.clip(turned / elapsed, -self.TURN_RATE, self.TURN_RATE))
        self.last_timestep, self.last_position, self.last_velocity = now, (x, y, yaw), velocity

    def get(self):
        return self.last_position

    def forecast(self, now):
        if self.last_position is None:
            return None
        x, y, yaw = self.last_position
        x_velocity, y_velocity, yaw_velocity = self.last_velocity
        ahead = now - self.last_timestep
        turn = yaw_velocity * ahead
        if x_velocity or y_velocity:
            travel = (math.degrees(math.atan2(y_velocity, x_velocity)) - yaw + 180) % 360 - 180
            turn = np.clip(turn, min(0, travel), max(0, travel))    # not past the direction of travel
        return x + x_velocity * ahead, y + y_velocity * ahead, yaw + turn


class DedupeQueue:
    """FIFO queue that drops items already waiting in it."""

    def __init__(self):
        self.items = []
        self.cond = threading.Condition()

    def put(self, item):
        with self.cond:
            if item not in self.items:
                self.items.append(item)
                self.cond.notify()

    def get(self, timeout=None):
        """The oldest item; None if none comes within `timeout` seconds (None: wait for ever)."""
        with self.cond:
            if not self.cond.wait_for(lambda: self.items, timeout):
                return None
            return self.items.pop(0)


def key_name(key):
    """A pynput key's name: '0'-'9' or 'a'-'z' whatever modifiers are held, or a special key's ("f13", "space");
    None for other keys.

    Digits and letters from the virtual-key code, since the character changes with modifiers (Ctrl + Z is '\\x1a')."""
    if isinstance(key, Key):
        return key.name
    vk = getattr(key, "vk", None)
    return chr(vk).lower() if vk is not None and (0x30 <= vk <= 0x39 or 0x41 <= vk <= 0x5A) else None


yaml_lock = threading.Lock()


def read_yaml_list(path, section):
    """The list `section` of the YAML file at `path`, as strings; [] if the file or the section is missing."""
    config = yaml.safe_load(path.read_text()) if path.exists() else None
    return [str(item) for item in (config or {}).get(section) or []]


def toggle_yaml_list(path, section, item):
    """Add `item` to the list `section` of the YAML file at `path`, or remove it if there; returns the new list.

    Rereads the file under a lock first, so the other sections stay as their writers left them.
    """
    with yaml_lock:
        config = (yaml.safe_load(path.read_text()) if path.exists() else None) or {}
        items = [str(i) for i in config.get(section) or []]
        if item in items:
            items.remove(item)
        else:
            items.append(item)
        config[section] = items
        path.write_text(yaml.safe_dump(config, sort_keys=False))
    return items


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
