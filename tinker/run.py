import math
import threading
from dataclasses import dataclass
import keyboard
import pywintypes
import uvicorn
import win32api
from fastapi import FastAPI, Request
from pynput import keyboard as pk
import time
from tinker.ability_monitor import TinkerAbilityMonitor
from tinker.repeat_key import TinkerRepeatKey
from utils.camera import CameraScreenProjection
from utils.memory import MemoryReader
from utils.window import hud_surface

LOOP_INTERVAL = 0.03

controller = pk.Controller()

KEY_BINDING = {                     # ability -> its Dota hotkey
    "tinker_warp_grenade":   "d",
    "tinker_laser":          "q",
    "tinker_deploy_turrets": "e",
    "tinker_rearm":          "f",
}
AUTOKEY = {                         # autokey -> the abilities it casts while held, in casting order: each loop casts the first castable one
    "o": ["tinker_warp_grenade", "tinker_deploy_turrets", "tinker_laser", "tinker_rearm"],
    "p": ["tinker_warp_grenade", "tinker_laser", "tinker_rearm"],
}
HERO_ONLY_KEY = "h"                 # Dota: while held, clicks and casts interact only with heroes; held with an autokey
SLOT_KEYS = {"slot0": "1", "slot1": "2", "slot2": "3", "slot3": "z", "slot4": "x", "slot5": "6"}  # GSI inventory slot -> Dota hotkey; slot6-8 are the backpack
REPEAT_ITEMS = {"item_blink", "item_overwhelming_blink", "item_swift_blink", "item_arcane_blink","item_cyclone", "item_wind_waker"}
EXTRA_ITEMS = {"item_sheepstick", "item_dagon", "item_dagon_2", "item_dagon_3", "item_dagon_4", "item_dagon_5", "item_ethereal_blade", "item_ghost", "item_soul_ring", "item_orchid", "item_bloodthorn"}         # pressed every loop
assert not set(KEY_BINDING.values()) & set(SLOT_KEYS.values()), "ability and item keys overlap"
TURRET_BEYOND = 10                  # Deploy Turrets aims this far past where Warp Flare lands its target, on the line from your hero through it
WARP_FLARE_BASE_CAST_RANGE = 700    # the game's scripts/npc/heroes/npc_dota_hero_tinker.txt
CAST_RANGE_BONUS = {"item_aether_lens": (225,), "item_enhancement_keen_eyed": (125, 135, 145)}    # by item_level (the game's scripts/npc/items.txt)
CAST_RANGE_SLOTS = {*SLOT_KEYS, "neutral0", "neutral1"}     # GSI slots where they work: inventory and neutral, not the backpack
WARP_FLARE_PUSH = 0.6               # Warp Flare's warp_distance_factor: the teleport is this share of the cast range at no distance, down to 0 at max range
WARP_FLARE_SPEED = 1900             # Warp Flare's projectile speed, world units per second
WARP_FLARE_ROOT = 2                 # Warp Flare's debuff_duration: its target stays rooted where it lands this many seconds


@dataclass
class WarpFlareLanding:
    """Warp Flare's target, and where and when it is predicted to land, from the cast; None while there is none."""
    position: tuple = None          # (x, y) the target is teleported to
    lands_at: float = None          # time.monotonic() it lands there
    entity: int = None              # address of the target entity
    entity_id: int = None           # its ID (MemoryReader.get_entity_id): another once the address holds another entity

    def clear(self):
        self.position = self.lands_at = self.entity = self.entity_id = None

    def predict_position(self, hero, entity, cast_range):
        """Predicts it for a Warp Flare going off now from `hero` (x, y, ...) at the entity at address `entity`: the
        target is teleported straight away from the hero, by WARP_FLARE_PUSH of the cast range at no distance, falling
        linearly to nothing at the cast range; it lands after the flare's flight to it."""
        target = memory.get_entity_position(entity)
        dx, dy = target[0] - hero[0], target[1] - hero[1]
        distance = math.hypot(dx, dy) or 1          # on top of each other: no direction to push in, dx and dy are 0
        push = WARP_FLARE_PUSH * max(0, cast_range - distance)
        self.lands_at = time.monotonic() + distance / WARP_FLARE_SPEED
        self.position = target[0] + dx / distance * push, target[1] + dy / distance * push
        self.entity, self.entity_id = entity, memory.get_entity_id(entity)

    def get_entity(self):
        """Address of the target entity; None without one, or once the address holds another entity (its ID is not
        entity_id any more) or nothing readable."""
        entity, entity_id = self.entity, self.entity_id         # once each: other threads set them
        if entity is None or entity_id is None or memory.get_entity_id(entity) != entity_id:
            return None
        else:
            return entity

    def get_position(self):
        """(x, y) the target lands at; None without a prediction, or once its root is over (WARP_FLARE_ROOT after it
        lands): the target may have walked off the spot."""
        position, lands_at = self.position, self.lands_at       # once each: other threads set them
        if position is None or lands_at is None or time.monotonic() > lands_at + WARP_FLARE_ROOT:
            return None
        return position


monitor = None                     # TinkerAbilityMonitor: the ability states off the screen
memory = None                       # MemoryReader of the running Dota
projection = None                   # CameraScreenProjection of Dota's client area
human_mouse = None                  # HumanMouseController: glides the real cursor like a hand would
repeat_key = None                   # TinkerRepeatKey: GSI sets its keys
extra_keys = set()                  # slot keys of the EXTRA_ITEMS in the inventory, set by GSI
autokey = None                      # the AUTOKEY key pressed last
held = threading.Event()            # that autokey is down
warp_flare_cast_range = WARP_FLARE_BASE_CAST_RANGE  # with the CAST_RANGE_BONUS items, set by GSI
warp_landing = WarpFlareLanding()   # of the last Warp Flare cast; cleared when an autokey goes down or up

app = FastAPI()


def press_and_release(key):
    controller.press(key)
    controller.release(key)


def on_trigger(key, event):
    global autokey

    # Ignore auto-repeat
    if event.event_type == keyboard.KEY_DOWN and held.is_set() and key == autokey:
        return
        
    warp_landing.clear()
    if event.event_type == keyboard.KEY_DOWN:
        autokey = key
        held.set()
        controller.press(HERO_ONLY_KEY)
    else:
        held.clear()
        controller.release(HERO_ONLY_KEY)


def predict_warp_landing():
    """Predicts where and when Warp Flare's target, the hovered enemy hero, lands (warp_landing), the flare going off
    now; keeps the last prediction while no enemy hero is hovered."""
    entity, hero = memory.get_hover_enemy_hero(), memory.get_hero_position()     # hero: (x, y, z, yaw)
    if entity and hero:
        warp_landing.predict_position(hero, entity, warp_flare_cast_range)


def move_cursor_to(pixel):
    """Glides the real cursor to a screen pixel (human_mouse)."""
    x, y = map(round, pixel)
    human_mouse.move_to((x, y))
    win32api.SetCursorPos((x, y))                   # the glide can end a pixel off


def cast_turrets():
    """Deploy Turrets. With a Warp Flare landing not expired: TURRET_BEYOND past where it lands its target, on the line
    from your hero through it; nothing where a click would not reach (world_to_screen): off screen, near its edges or
    under the HUD. Otherwise where the cursor is, as is."""
    landing = warp_landing.get_position()
    if landing is None:
        press_and_release(KEY_BINDING["tinker_deploy_turrets"])
        return

    (x, y), hero = landing, memory.get_hero_position()     # hero: (x, y, z, yaw)
    dx, dy = x - hero[0], y - hero[1]
    distance = math.hypot(dx, dy) or 1          # on top of each other: aim at the landing itself
    screen_position = projection.world_to_screen(memory.get_view_matrix(), (x + dx / distance * TURRET_BEYOND, y + dy / distance * TURRET_BEYOND))
    if not screen_position:
        return
    move_cursor_to(screen_position)
    press_and_release(KEY_BINDING["tinker_deploy_turrets"])


def cast_laser():
    """Laser. While Warp Flare's target is still the entity it was (warp_landing.get_entity()): with the real cursor
    glided onto where it stands now; nothing where a click there would not reach. Otherwise where the cursor is, as is."""
    entity = warp_landing.get_entity()
    if entity is None:
        press_and_release(KEY_BINDING["tinker_laser"])
        return

    screen_position = projection.world_to_screen(memory.get_view_matrix(), memory.get_entity_position(entity))
    if not screen_position:
        return
    move_cursor_to(screen_position)
    press_and_release(KEY_BINDING["tinker_laser"])


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
        monitor.activate()            # keeps it watching while an autokey is held; all castable until its first frame
        combo = AUTOKEY[autokey]
        castable = [a for a in combo if monitor.states[a].castable]    # casting counts as not castable
        to_fire = castable[0] if castable else combo[0]      # none castable: the first
        if to_fire == "tinker_deploy_turrets":
            cast_turrets()
        elif to_fire == "tinker_laser":
            cast_laser()
        else:
            press_and_release(KEY_BINDING[to_fire])
        for k in extra_keys:
            press_and_release(k)

        time.sleep(LOOP_INTERVAL)


if __name__ == "__main__":
    monitor = TinkerAbilityMonitor()  # first: it makes the process DPI aware
    warp_flare = monitor.states["tinker_warp_grenade"]
    warp_flare.on_casted = predict_warp_landing         # the flare goes off now: the prediction that counts
    warp_flare.on_casting = predict_warp_landing        # approximate: a backup for no enemy hero hovered as it goes off
    from humanmouse import HumanMouseController     # after that: pyautogui's import would set the DPI awareness first
    human_mouse = HumanMouseController(speed_factor=1000)
    memory = MemoryReader()
    projection = CameraScreenProjection(hud_surface()[0])
    repeat_key = TinkerRepeatKey()
    for key in AUTOKEY:
        keyboard.hook_key(key, lambda event, key=key: on_trigger(key, event), suppress=True)
    threading.Thread(target=worker, daemon=True).start()
    uvicorn.run(app, host="127.0.0.1", port=3000, log_level="warning")     # GSI posts; Ctrl + C quits
