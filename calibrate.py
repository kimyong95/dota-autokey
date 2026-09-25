"""Draw a Dota ground cross where utils.CameraScreenProjection says the cursor points.

Run: uv run calibrate.py   (Dota with -tools, a hero in game, data/map-height.npy)
Every INTERVAL seconds: read the camera position and the cursor, project the cursor to the
ground, and `cl_drawcross` there. If the projection is right, the cross stays under the
cursor. Ctrl+C to exit.
"""
import re
import time

import win32api

import utils
from vconsole_client import VConsoleClient
from window_utils import hud_surface

INTERVAL = 0.5          # seconds between updates
CAMERA_RE = re.compile(r"Camera position:\s+(\S+)\s+(\S+)\s+(\S+)")

projection = utils.CameraScreenProjection(hud_surface()[0])
with VConsoleClient() as vc:
    while True:
        camera = tuple(map(float, CAMERA_RE.search(vc.run_cmd("dota_camera_get_pos")).groups()))
        cursor = win32api.GetCursorPos()
        x, y, z = projection.screen_to_world(camera, cursor)
        vc.run_cmd(f"cl_drawcross {x:.0f} {y:.0f} {z}")
        print(f"cursor {cursor} -> world ({x:.0f}, {y:.0f}, {z})   ", end="\r", flush=True)
        time.sleep(INTERVAL)
