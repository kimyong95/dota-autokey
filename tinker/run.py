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
    "tinker_laser":          "q",
    "tinker_deploy_turrets": "e",
    "tinker_rearm":          "f",
}
AUTOKEY = "o"
HERO_ONLY_KEY = "h"                 # Dota: while held, clicks and casts interact only with heroes; held with AUTOKEY
SLOT_KEYS = {"slot0": "1", "slot1": "2", "slot2": "3", "slot3": "z", "slot4": "x", "slot5": "6"}  # GSI inventory slot -> Dota hotkey; slot6-8 are the backpack
REPEAT_ITEMS = {"item_blink", "item_overwhelming_blink", "item_swift_blink", "item_arcane_blink","item_cyclone", "item_wind_waker"}
EXTRA_ITEMS = {"item_sheepstick", "item_dagon", "item_dagon_2", "item_dagon_3", "item_dagon_4", "item_dagon_5", "item_ethereal_blade", "item_ghost", "item_soul_ring", "item_orchid", "item_bloodthorn"}         # pressed every loop
assert not set(COMBO.values()) & set(SLOT_KEYS.values()), "combo and item keys overlap"
WARP_FLARE_BASE_CAST_RANGE = 700    # the game's scripts/npc/heroes/npc_dota_hero_tinker.txt
CAST_RANGE_BONUS = {"item_aether_lens": (225,), "item_enhancement_keen_eyed": (125, 135, 145)}    # by item_level (the game's scripts/npc/items.txt)
CAST_RANGE_SLOTS = {*SLOT_KEYS, "neutral0", "neutral1"}     # GSI slots where they work: inventory and neutral, not the backpack
WARP_FLARE_PUSH = 0.6               # Warp Flare's warp_distance_factor: the teleport is this share of the cast range at no distance, down to 0 at max range
WARP_FLARE_SPEED = 1900             # Warp Flare's projectile speed, world units per second
LANDING_GRACE = 1.0                 # cast_turrets aims at v until this many seconds past t, the flare's estimated landing
TURRET_BEYOND = 30                  # Deploy Turrets aims this far past v, on the line from your hero through v


class WarpFlareCastState:
    """Warp Flare's target, from the cast point of an ability before Deploy Turrets in COMBO until Warp Flare is
    available again (reset):
        u  the target's (x, y, z): the enemy hero last hovered while one of those was casting, or as Warp Flare went
           off; None if none was
        v  (x, y) the target is teleported to, by warp_flare_landing from u as the flare went off; None before that
           and without u
        t  time.monotonic() the flare is estimated to land: as it went off + its flight to u at WARP_FLARE_SPEED
    """

    def __init__(self):
        self.reset()

    def reset(self):
        self.u = self.v = self.t = None


monitor = None                     # TinkerAbilityMonitor: the ability states off the screen
memory = None                       # MemoryReader of the running Dota
projection = None                   # CameraScreenProjection of Dota's client area
human_mouse = None                  # HumanMouseController: glides the real cursor like a hand would
repeat_key = None                   # TinkerRepeatKey: GSI sets its keys
extra_keys = set()                  # slot keys of the EXTRA_ITEMS in the inventory, set by GSI
held = threading.Event()            # AUTOKEY is down
suppress_rearm = False
warp_flare_cast_range = WARP_FLARE_BASE_CAST_RANGE  # with the CAST_RANGE_BONUS items, set by GSI
warp_flare_cast_state = WarpFlareCastState()   # set by the ability hooks, read by cast_turrets

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


def hovered_target():
    """The hovered unit's position, else the target stored so far: the cursor may be off it for a moment."""
    return memory.get_hover_enemy_hero_position() or warp_flare_cast_state.u


def on_casting_before_turrets():
    """on_casting of the abilities before Deploy Turrets in COMBO: stores the hovered target as u."""
    warp_flare_cast_state.u = hovered_target()


def on_casted_warp_flare():
    """Stores the target as u, where the flare teleports it as v, and when it lands as t."""
    state = warp_flare_cast_state
    hero, state.u = memory.get_hero_position(), hovered_target()     # hero: (x, y, z, yaw)
    if hero and state.u:
        state.v = warp_flare_landing(hero, state.u, warp_flare_cast_range)
        state.t = time.monotonic() + math.dist(hero[:2], state.u[:2]) / WARP_FLARE_SPEED


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
    """From a Warp Flare with a target (v) until LANDING_GRACE past its landing (t), and with everything to aim with,
    presses Deploy Turrets' key with the real cursor held TURRET_BEYOND past where the flare puts the target, on the
    line from your hero now through v. Otherwise only right-clicks where the cursor is: no cursor move, no cast key."""
    v, t = warp_flare_cast_state.v, warp_flare_cast_state.t     # once: the hooks set them on the monitor's thread
    hero, matrix = memory.get_hero_position(), memory.get_view_matrix()      # hero: (x, y, z, yaw)
    if v and t and time.monotonic() <= t + LANDING_GRACE and hero and matrix is not None:
        dx, dy = v[0] - hero[0], v[1] - hero[1]
        distance = math.hypot(dx, dy) or 1          # on top of each other: aim at v itself
        aim = v[0] + dx / distance * TURRET_BEYOND, v[1] + dy / distance * TURRET_BEYOND
        with cursor_held_at(projection.world_to_screen(matrix, aim)):
            press_and_release(COMBO["tinker_deploy_turrets"])
    else:
        mouse.click(pm.Button.right)


@app.post("/")
async def gsi(request: Request):
    global extra_keys, warp_flare_cast_range
    items = (await request.json()).get("items")
    if items:
        names = {key: items.get(slot, {}).get("name") for slot, key in SLOT_KEYS.items()}
        repeat_key.keys = {key for key, name in names.items() if name in REPEAT_ITEMS}
        extra_keys = {key for key, name in names.items() if name in EXTRA_ITEMS}
        levels = {items.get(slot, {}).get("name"): items.get(slot, {}).get("item_level", 1) for slot in CAST_RANGE_SLOTS}  # a second copy adds nothing
        bonus = sum(by_level[min(levels[name], len(by_level)) - 1] for name, by_level in CAST_RANGE_BONUS.items() if name in levels)
        warp_flare_cast_range = WARP_FLARE_BASE_CAST_RANGE + bonus
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
    for ability in list(COMBO)[:list(COMBO).index("tinker_deploy_turrets")]:      # cast at what the turrets aim past
        monitor.states[ability].on_casting = on_casting_before_turrets
    monitor.states["tinker_warp_grenade"].on_casted = on_casted_warp_flare
    monitor.states["tinker_warp_grenade"].on_available = warp_flare_cast_state.reset
    repeat_key = TinkerRepeatKey()
    keyboard.hook_key(AUTOKEY, on_trigger, suppress=True)
    threading.Thread(target=worker, daemon=True).start()
    pk.Listener(on_press=on_press).start()
    uvicorn.run(app, host="127.0.0.1", port=3000, log_level="warning")     # GSI posts; Ctrl + C quits
