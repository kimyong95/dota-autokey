"""Measure the ground height of the whole Dota map, every unit, and save it as a numpy array.

Run: uv run python measure-map-data.py [--out data/map-height.npy]
Needs dota2.exe launched with -tools (VConsole on 29000) and a map loaded.

Covers the full world bounds (GetWorldMinX..GetWorldMaxX, GetWorldMinY..GetWorldMaxY).
Output: int16 array with zero-based, offset indices: `z[x - x_min][y - y_min]` =
GetGroundHeight(Vector(x, y, 0)) rounded to the nearest unit and clamped at 0, where x_min /
y_min are GetWorldMinX() / GetWorldMinY() (both -16352 on the standard map, printed at
start). Points outside the playable terrain (engine height -16384) read as 0.

MEASURE_MAP_LUA is copied into Dota's scripts/vscripts folder and loaded with require; each
call to its MeasureMapRow measures one CHUNK of a row z[x].
"""
import argparse
from pathlib import Path

import numpy as np
from tqdm import tqdm

from vconsole_client import VConsoleClient

CHUNK = 3000
VSCRIPTS = Path(r"C:\Program Files (x86)\Steam\steamapps\common\dota 2 beta\game\dota\scripts\vscripts")

MEASURE_MAP_LUA = """\
-- Heights along the row x, from y = y0 to y1, rounded to whole units and clamped at 0: 'z,z,z,...'.
function MeasureMapRow(x, y0, y1)
    local out = {}
    for y = y0, y1 do
        out[#out + 1] = math.max(0, math.floor(GetGroundHeight(Vector(x, y, 0), nil) + 0.5))
    end
    return table.concat(out, ',')
end
"""


def query(vc: VConsoleClient, expr: str) -> str:
    while (r := vc.run_lua(expr, timeout=10.0)) is None:
        print("  timeout, retrying", flush=True)
    return r


def load_lua(vc: VConsoleClient) -> None:
    """Copy MEASURE_MAP_LUA into Dota's vscripts folder and (re)load it."""
    (VSCRIPTS / "measure_map.lua").write_text(MEASURE_MAP_LUA)
    query(vc, "(function() package.loaded.measure_map = nil require('measure_map') return 1 end)()")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, default=Path("data/map-height.npy"))
    args = ap.parse_args()

    with VConsoleClient() as vc:
        vc.run_cmd("dota_creeps_no_spawning 1")  # no lane creeps, so towers survive the long run
        load_lua(vc)
        x_min, x_max, y_min, y_max = (
            int(float(query(vc, f"{fn}()"))) for fn in ("GetWorldMinX", "GetWorldMaxX", "GetWorldMinY", "GetWorldMaxY")
        )
        nx, ny = x_max - x_min + 1, y_max - y_min + 1
        print(f"x {x_min}..{x_max}, y {y_min}..{y_max} -> {nx} x {ny}", flush=True)

        args.out.parent.mkdir(parents=True, exist_ok=True)
        z = np.lib.format.open_memmap(args.out, mode="w+", dtype=np.int16, shape=(nx, ny))
        for i in tqdm(range(nx), desc="rows", position=0):
            for y0 in tqdm(range(y_min, y_max + 1, CHUNK), desc="chunks", position=1, leave=False):
                y1 = min(y0 + CHUNK - 1, y_max)
                chunk = np.array(query(vc, f"MeasureMapRow({x_min + i}, {y0}, {y1})").split(","), np.int16)
                if chunk.size != y1 - y0 + 1:
                    raise RuntimeError(f"x={x_min + i} y={y0}..{y1}: got {chunk.size} values: {chunk}")
                z[i, y0 - y_min:y1 - y_min + 1] = chunk
        z.flush()
    print(f"saved {args.out} {z.shape}", flush=True)


if __name__ == "__main__":
    main()
