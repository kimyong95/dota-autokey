from pathlib import Path

import numpy as np


class CameraScreenProjection:
    """Screen pixel <-> world point on Dota's terrain, through the view matrix.

        projection = CameraScreenProjection(viewport)
        x, y, z = projection.screen_to_world(matrix, (sx, sy))
        sx, sy = projection.world_to_screen(matrix, (x, y))

    matrix is the view matrix from MemoryReader.get_view_matrix(): clip = matrix @ (x, y, z, 1), and clip x / w and
    y / w run from -1 to 1 across the viewport, y up. viewport is (left, top, width, height) of the full
    3D render, including behind the HUD; screen pixels are in the same space, origin top-left, y down.
    World z always comes from the measured terrain (data/map-height.npy from init/measure-map-height.py).
    """

    MAP_MIN = -16352        # world x / y of index 0 in map-height.npy (GetWorldMinX / GetWorldMinY)

    def __init__(self, viewport):
        left, top, width, height = viewport
        self.center = (left + width / 2, top + height / 2)      # where clip x / w and y / w are 0
        self.half_size = (width / 2, height / 2)
        self.heights = np.load(Path(__file__).parents[1] / "data" / "map-height.npy", mmap_mode="r")

    def height(self, x, y):
        """Terrain height at world (x, y)."""
        return int(self.heights[round(x) - self.MAP_MIN, round(y) - self.MAP_MIN])

    def world_to_screen(self, matrix, world):
        """Screen pixel of the terrain point at world (x, y)."""
        x, y = world[:2]
        clip = matrix @ (x, y, self.height(x, y), 1)
        return (self.center[0] + self.half_size[0] * clip[0] / clip[3],
                self.center[1] - self.half_size[1] * clip[1] / clip[3])

    def screen_to_world(self, matrix, screen):
        """Terrain point (x, y, z) under a screen pixel.

        The pixel's ray is every point whose clip x / w and y / w are the pixel's: rows @ (x, y, z, 1) = 0.
        Meets it with the plane at a guessed height, then again at the terrain height found there; a few
        rounds settle on the terrain.
        """
        ndc = ((screen[0] - self.center[0]) / self.half_size[0], (self.center[1] - screen[1]) / self.half_size[1])
        rows = matrix[:2] - np.outer(ndc, matrix[3])
        z = 0
        for _ in range(4):
            x, y = np.linalg.solve(rows[:, :2], -rows[:, 3] - rows[:, 2] * z)
            z = self.height(x, y)
        return x, y, z
