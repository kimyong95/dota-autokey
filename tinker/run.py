import ctypes
import ctypes.wintypes
import math
import threading
from contextlib import contextmanager
import keyboard
import uvicorn
import win32api
from fastapi import FastAPI, Request
from pynput import keyboard as pk
from pynput import mouse as pm
import time
from tinker.ability_monitor import TinkerAbilityMonitor
from tinker.repeat_key import TinkerRepeatKey
from utils.camera import CameraScreenProjection
from utils.keyboard import key_name
from utils.memory import MemoryReader
from utils.window import hud_surface

LOOP_INTERVAL = 0.03

controller = pk.Controller()
mouse = pm.Controller()

COMBO = {                           # ability -> hotkey, in casting order: each loop casts the first castable one
    "tinker_warp_grenade":   "d",
    "tinker_deploy_turrets": "e",
    "tinker_laser":          "q",
    "tinker_rearm":          "f",
}
AUTOKEY = "o"
HERO_ONLY_KEY = "h"                 # Dota: while held, clicks and casts interact only with heroes; held with AUTOKEY
SLOT_KEYS = {"slot0": "1", "slot1": "2", "slot2": "3", "slot3": "z", "slot4": "x", "slot5": "6"}  # GSI inventory slot -> Dota hotkey; slot6-8 are the backpack
REPEAT_ITEMS = {"item_blink", "item_overwhelming_blink", "item_swift_blink", "item_arcane_blink","item_cyclone", "item_wind_waker"}
EXTRA_ITEMS = {"item_sheepstick", "item_dagon", "item_dagon_2", "item_dagon_3", "item_dagon_4", "item_dagon_5", "item_ethereal_blade", "item_ghost", "item_soul_ring", "item_orchid", "item_bloodthorn"}         # pressed every loop
assert not set(COMBO.values()) & set(SLOT_KEYS.values()), "combo and item keys overlap"
CAST_RANGE = {"tinker_warp_grenade": 700, "tinker_deploy_turrets": 600}   # base (the game's scripts/npc/heroes/npc_dota_hero_tinker.txt)
CAST_RANGE_BONUS = {"item_aether_lens": (225,), "item_enhancement_keen_eyed": (125, 135, 145)}    # by item_level (the game's scripts/npc/items.txt)
CAST_RANGE_SLOTS = [*SLOT_KEYS, "neutral0", "neutral1"]     # GSI slots where they work: inventory and neutral, not the backpack
WARP_FLARE_PUSH = 0.6               # Warp Flare's warp_distance_factor: the teleport is this share of the cast range at no distance, down to 0 at max range
TURRET_BEYOND = 30                  # Deploy Turrets aims this far past v, on the line from your hero through v

monitor = None                      # TinkerAbilityMonitor: the ability states off the screen
memory = None                       # MemoryReader of the running Dota
projection = None                   # CameraScreenProjection of Dota's client area
human_mouse = None                  # HumanMouseController: glides the real cursor like a hand would
repeat_key = None                   # TinkerRepeatKey: GSI sets its keys
extra_keys = []                     # slot keys of the EXTRA_ITEMS in the inventory, set by GSI
held = threading.Event()            # AUTOKEY is down
suppress_rearm = False
cast_range = dict(CAST_RANGE)       # ability -> its cast range with the CAST_RANGE_BONUS items, set by GSI
WARP_FLARE_CAST_STATE = None        # as Warp Flare went off, until it is available again:
                                    #   "u": the target's (x, y, z): the unit hovered then; None if none
                                    #   "v": (x, y) the target is teleported to, by warp_flare_landing; None without u

app = FastAPI()


def press_and_release(key):
    controller.press(key)
    controller.release(key)


def on_trigger(event):
    global suppress_rearm
    if event.event_type == keyboard.KEY_UP:
        held.clear()
        controller.release(HERO_ONLY_KEY)
    elif not held.is_set():         # a fresh press, not auto-repeat
        suppress_rearm = False
        held.set()
        controller.press(HERO_ONLY_KEY)


def on_press(key, injected):
    global suppress_rearm
    if not injected and held.is_set() and key_name(key) != AUTOKEY:
        suppress_rearm = True         # a key of the user's own while the autokey runs, not one of our sent keys


def warp_flare_landing(x, u, cast_range):
    """(x, y) where Warp Flare teleports a target at `u` cast from `x`: straight away from x, by WARP_FLARE_PUSH of
    the cast range at no distance, falling linearly to nothing at the cast range. Lands on the ground there."""
    dx, dy = u[0] - x[0], u[1] - x[1]
    distance = math.hypot(dx, dy)
    if distance == 0:                           # no direction to push in
        return u[:2]
    push = WARP_FLARE_PUSH * max(0, cast_range - distance)
    return u[0] + dx / distance * push, u[1] + dy / distance * push


def on_casted_warp_flare():
    global WARP_FLARE_CAST_STATE
    hero, u = memory.get_hero_position(), memory.get_hover_position()     # hero: (x, y, z, yaw)
    v = hero and u and warp_flare_landing(hero, u, cast_range["tinker_warp_grenade"])
    WARP_FLARE_CAST_STATE = {"u": u, "v": v}
    print(f"Warp Flare casted: {WARP_FLARE_CAST_STATE}", flush=True)


def on_available_warp_flare():
    global WARP_FLARE_CAST_STATE
    WARP_FLARE_CAST_STATE = None


@contextmanager
def cursor_held_at(pixel):
    """Glides the real cursor to a screen pixel (human_mouse), then holds it exactly there until the block ends: confined
    to that one pixel (ClipCursor), so the user's mouse cannot move it in between. Clamped into Dota's client area, as
    SetCursorPos clamps into the screen. The previous confinement (Dota's "lock mouse to window") comes back after."""
    (cx, cy), (half_width, half_height) = projection.center, projection.half_size
    x = round(min(max(pixel[0], cx - half_width), cx + half_width - 1))
    y = round(min(max(pixel[1], cy - half_height), cy + half_height - 1))
    human_mouse.move_to((x, y))
    previous = ctypes.wintypes.RECT()
    ctypes.windll.user32.GetClipCursor(ctypes.byref(previous))     # pywin32 has no GetClipCursor
    win32api.ClipCursor((x, y, x + 1, y + 1))
    win32api.SetCursorPos((x, y))                   # the glide can end a pixel off, or the user nudge it
    try:
        yield
    finally:
        win32api.ClipCursor((previous.left, previous.top, previous.right, previous.bottom))


def cast_turrets():
    """With a Warp Flare target and everything to aim with, presses Deploy Turrets' key with the real cursor held
    TURRET_BEYOND past where the flare puts the target (v), on the line from your hero now through v. Otherwise only
    right-clicks where the cursor is: no cursor move, no cast key."""
    state = WARP_FLARE_CAST_STATE
    hero, matrix = memory.get_hero_position(), memory.get_view_matrix()      # hero: (x, y, z, yaw)
    if state and (v := state["v"]) and hero and matrix is not None:
        dx, dy = v[0] - hero[0], v[1] - hero[1]
        distance = math.hypot(dx, dy) or 1          # on top of each other: aim at v itself
        aim = v[0] + dx / distance * TURRET_BEYOND, v[1] + dy / distance * TURRET_BEYOND
        with cursor_held_at(projection.world_to_screen(matrix, aim)):
            press_and_release(COMBO["tinker_deploy_turrets"])
    else:
        mouse.click(pm.Button.right)

    print(WARP_FLARE_CAST_STATE)


@app.post("/")
async def gsi(request: Request):
    global extra_keys, cast_range
    items = (await request.json()).get("items")
    if items:
        names = {key: items.get(slot, {}).get("name") for slot, key in SLOT_KEYS.items()}
        repeat = [key for key, name in names.items() if name in REPEAT_ITEMS]
        extra = [key for key, name in names.items() if name in EXTRA_ITEMS]
        repeat_key.keys, extra_keys = repeat, extra
        levels = {items.get(slot, {}).get("name"): items.get(slot, {}).get("item_level", 1) for slot in CAST_RANGE_SLOTS}  # a second copy adds nothing
        bonus = sum(bonus[min(levels[name], len(bonus)) - 1] for name, bonus in CAST_RANGE_BONUS.items() if name in levels)
        cast_range = {ability: base + bonus for ability, base in CAST_RANGE.items()}
    return {}


def worker():
    while True:
        held.wait()
        monitor.activate()            # keeps it watching while AUTOKEY is held; all castable until its first frame
        castable = [a for a in COMBO if monitor.states[a].castable]    # casting counts as not castable
        to_fire = castable[0] if castable else next(iter(COMBO))      # none castable: the first
        if suppress_rearm and to_fire == "tinker_rearm":
            continue
        
        if to_fire == "tinker_deploy_turrets":
            cast_turrets()
        else:
            press_and_release(COMBO[to_fire])
        for k in extra_keys:
            press_and_release(k)

        time.sleep(LOOP_INTERVAL)


if __name__ == "__main__":
    monitor = TinkerAbilityMonitor()  # first: it makes the process DPI aware
    from humanmouse import HumanMouseController     # after that: pyautogui's import would set the DPI awareness first
    human_mouse = HumanMouseController(speed_factor=1000)
    memory = MemoryReader()
    projection = CameraScreenProjection(hud_surface()[0])
    monitor.states["tinker_warp_grenade"].on_casted = on_casted_warp_flare
    monitor.states["tinker_warp_grenade"].on_available = on_available_warp_flare
    repeat_key = TinkerRepeatKey()
    keyboard.hook_key(AUTOKEY, on_trigger, suppress=True)
    threading.Thread(target=worker, daemon=True).start()
    pk.Listener(on_press=on_press).start()
    uvicorn.run(app, host="127.0.0.1", port=3000, log_level="warning")     # GSI posts; Ctrl + C quits
