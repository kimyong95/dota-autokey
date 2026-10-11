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
TURRET_BEYOND = 10                  # Deploy Turrets aims this far past Warp Flare's target, on the line from your hero through it


monitor = None                     # TinkerAbilityMonitor: the ability states off the screen
memory = None                       # MemoryReader of the running Dota
projection = None                   # CameraScreenProjection of Dota's client area
human_mouse = None                  # HumanMouseController: glides the real cursor like a hand would
repeat_key = None                   # TinkerRepeatKey: GSI sets its keys
extra_keys = set()                  # slot keys of the EXTRA_ITEMS in the inventory, set by GSI
held = threading.Event()            # AUTOKEY is down
suppress_rearm = False
warp_flare_target = None            # address of the enemy hero entity hovered as Warp Flare started casting; None if none was, and once Warp Flare is available again

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


def on_casting_warp_flare():
    global warp_flare_target
    warp_flare_target = memory.get_hover_enemy_hero()


def on_available_warp_flare():
    global warp_flare_target
    warp_flare_target = None


@contextmanager
def cursor_held_at(pixel):
    """Glides the real cursor to a screen pixel (human_mouse), then holds it exactly there until the block ends: confined
    to that one pixel (ClipCursor), so the user's mouse cannot move it in between. The previous confinement (Dota's
    "lock mouse to window") comes back after."""
    x, y = map(round, pixel)
    human_mouse.move_to((x, y))
    previous = ctypes.wintypes.RECT()
    ctypes.windll.user32.GetClipCursor(ctypes.byref(previous))     # pywin32 has no GetClipCursor
    win32api.ClipCursor((x, y, x + 1, y + 1))
    win32api.SetCursorPos((x, y))                   # the glide can end a pixel off, or the user nudge it
    try:
        yield
    finally:
        win32api.ClipCursor((previous.left, previous.top, previous.right, previous.bottom))


def cast_at(ability, point):
    """Presses the ability's key with the real cursor held on the terrain at world point (x, y); nothing where a click
    would not reach it (world_to_screen): off screen, near its edges or under the HUD."""
    pixel = projection.world_to_screen(memory.get_view_matrix(), point)
    if not pixel:
        return
    with cursor_held_at(pixel):
        press_and_release(COMBO[ability])


def cast_laser():
    """Laser at Warp Flare's target, the cursor held where it stands; nothing while a click there would not reach it.
    Without a target, Laser where the cursor is."""
    target = warp_flare_target      # once: the hooks set it on the monitor's thread
    if target:
        cast_at("tinker_laser", memory.get_entity_position(target))
    else:
        press_and_release(COMBO["tinker_laser"])


def cast_turrets():
    """Deploy Turrets TURRET_BEYOND past where Warp Flare's target stands, on the line from your hero through it;
    nothing while a click there would not reach it. Without a target, only right-clicks where the cursor is: no
    cursor move, no cast key."""
    target = warp_flare_target      # once: the hooks set it on the monitor's thread
    if target:
        (x, y, _), hero = memory.get_entity_position(target), memory.get_hero_position()     # hero: (x, y, z, yaw)
        dx, dy = x - hero[0], y - hero[1]
        distance = math.hypot(dx, dy) or 1          # on top of each other: aim at the target itself
        cast_at("tinker_deploy_turrets", (x + dx / distance * TURRET_BEYOND, y + dy / distance * TURRET_BEYOND))
    else:
        mouse.click(pm.Button.right)


@app.post("/")
async def gsi(request: Request):
    global extra_keys
    items = (await request.json()).get("items")
    if items:
        names = {key: items.get(slot, {}).get("name") for slot, key in SLOT_KEYS.items()}
        repeat_key.keys = {key for key, name in names.items() if name in REPEAT_ITEMS}
        extra_keys = {key for key, name in names.items() if name in EXTRA_ITEMS}
    return {}


def worker():
    while True:
        held.wait()
        monitor.activate()            # keeps it watching while AUTOKEY is held; all castable until its first frame
        castable = [a for a in COMBO if monitor.states[a].castable]    # casting counts as not castable
        to_fire = castable[0] if castable else next(iter(COMBO))      # none castable: the first
        if suppress_rearm and to_fire == "tinker_rearm":
            continue

        if to_fire == "tinker_laser":
            cast_laser()
        elif to_fire == "tinker_deploy_turrets":
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
    monitor.states["tinker_warp_grenade"].on_casting = on_casting_warp_flare
    monitor.states["tinker_warp_grenade"].on_available = on_available_warp_flare
    repeat_key = TinkerRepeatKey()
    keyboard.hook_key(AUTOKEY, on_trigger, suppress=True)
    threading.Thread(target=worker, daemon=True).start()
    pk.Listener(on_press=on_press).start()
    uvicorn.run(app, host="127.0.0.1", port=3000, log_level="warning")     # GSI posts; Ctrl + C quits
