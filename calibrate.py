"""Draw a Dota ground cross where utils.screen_to_world_coordinate says the cursor points.

Run: uv run calibrate.py   (Dota with -tools, a hero in game, data/map-height.npy)
Every INTERVAL seconds: read the camera look-at and the cursor, project the cursor to the
ground, and `cl_drawcross` there. If the projection is right, the cross stays under the
cursor. Ctrl+C to exit.
"""
import re
import time

import numpy as np
import win32api

import utils
from vconsole_client import VConsoleClient
from window_utils import hud_surface

INTERVAL = 0.5          # seconds between updates
MAP_MIN = -16352        # world coordinate of index 0 in map-height.npy
HEIGHTS = np.load("data/map-height.npy", mmap_mode="r")
CAMERA_RE = re.compile(r"Camera look-at position:\s+(\S+)\s+(\S+)\s+(\S+)")

with VConsoleClient() as vc:
    while True:
        camera = tuple(map(float, CAMERA_RE.search(vc.run_cmd("dota_camera_get_lookatpos")).groups()))
        cursor = win32api.GetCursorPos()
        bounds = hud_surface()[0]
        # screen_to_world_coordinate meets the flat plane z; re-aim at the terrain height there.
        z = camera[2]
        for _ in range(3):
            x, y, _ = utils.screen_to_world_coordinate(cursor, camera, bounds, ground_z=z)
            z = int(HEIGHTS[round(x) - MAP_MIN, round(y) - MAP_MIN])
        vc.run_cmd(f"cl_drawcross {x:.0f} {y:.0f} {z}")
        print(f"cursor {cursor} -> world ({x:.0f}, {y:.0f}, {z})   ", end="\r", flush=True)
        time.sleep(INTERVAL)
