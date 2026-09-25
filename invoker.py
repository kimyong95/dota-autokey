import signal
import threading
import time
import keyboard
from keyboard import KEY_UP, KEY_DOWN
import uvicorn
from fastapi import FastAPI, Request
from PySide6.QtWidgets import QApplication

import invoker_hub_overlay
import invoker_icewall_overlay
import invoker_tornado_overlay
import utils
from utils import updated_abilities

WAIT_INVOKE_TIMEOUT = 0.2
SLOT_KEYS = {"ability3": "c", "ability4": "v"}   # invoked slot -> cast key

KEY_BINDING = {
    "invoker_quas": "j", "invoker_wex": "k", "invoker_exort": "l", "invoker_invoke": 12,
}

AUTOKEY = {
    "q": "invoker_ice_wall",
    "w": "invoker_sun_strike",
    "e": "invoker_chaos_meteor",
    "r": "invoker_deafening_blast",
    "d": "invoker_forge_spirit", "f": "invoker_alacrity",
    "o": "invoker_cold_snap", "p": "invoker_tornado",
    "4": "invoker_emp", "5": "invoker_ghost_walk",
}

INVOKE_RECIPES = {
    "invoker_cold_snap":         ["invoker_quas",  "invoker_quas",  "invoker_quas",  "invoker_invoke"],
    "invoker_forge_spirit":      ["invoker_quas",  "invoker_exort", "invoker_exort", "invoker_invoke"],
    "invoker_alacrity":          ["invoker_wex",   "invoker_wex",   "invoker_exort", "invoker_invoke"],
    "invoker_sun_strike":        ["invoker_exort", "invoker_exort", "invoker_exort", "invoker_invoke"],
    "invoker_ghost_walk":        ["invoker_quas",  "invoker_quas",  "invoker_wex",   "invoker_invoke"],
    "invoker_ice_wall":          ["invoker_quas",  "invoker_quas",  "invoker_exort", "invoker_invoke"],
    "invoker_tornado":           ["invoker_quas",  "invoker_wex",   "invoker_wex",   "invoker_invoke"],
    "invoker_emp":               ["invoker_wex",   "invoker_wex",   "invoker_wex",   "invoker_invoke"],
    "invoker_chaos_meteor":      ["invoker_wex",   "invoker_exort", "invoker_exort", "invoker_invoke"],
    "invoker_deafening_blast":   ["invoker_quas",  "invoker_wex",   "invoker_exort", "invoker_invoke"],
}

CAST_IMEDIATELY = set(INVOKE_RECIPES) - {"invoker_ice_wall", "invoker_sun_strike", "invoker_tornado"}

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

    def get(self):
        with self.cond:
            while not self.items:
                self.cond.wait()
            return self.items.pop(0)


invoked = {}                       # spell -> cast key, updated by GSI
ability_levels = {}                # ability name -> level, updated by GSI
hero_minimap_state = None          # (x, y, yaw) from GSI's minimap block; yaw 0 = +x, 90 = +y
tornado_cast_state = None          # (release time, hero x, hero y) of the last Tornado cast
ready_at = {}                      # spell -> monotonic time it comes off cooldown
cooldown_totals = {}               # duration observed at the start of each cooldown
state_lock = threading.Lock()
event_queue = DedupeQueue()        # trigger events awaiting the worker
hub_overlay = None                 # created on the Qt (main) thread
icewall_overlay = None
tornado_overlay = None

app = FastAPI()


def track_cooldown(abilities, prev_abilities):
    # the tick where "cooldown" changed is the instant the reported whole-second
    # value became true, so now + cooldown is accurate
    now = time.monotonic()
    updated = updated_abilities(abilities, prev_abilities, "cooldown")
    if any(
        "name" not in prev_abilities[slot]                               # not a slot swap
        and prev_abilities[slot]["cooldown"] - ability["cooldown"] > 1   # cooldown jumped down
        for slot, ability in updated.items()
    ):
        ready_at.clear()    # refresher: also frees the 8 spells GSI never shows us
        cooldown_totals.clear()
    for ability in updated.values():
        spell, left = ability["name"], ability["cooldown"]
        previous = max(0.0, ready_at.get(spell, 0) - now)
        if left <= 0:
            cooldown_totals.pop(spell, None)
        elif spell not in cooldown_totals or left > previous + 0.5:
            cooldown_totals[spell] = left
        ready_at[spell] = now + left


def hub_overlay_state():
    """One consistent snapshot: {spell: (remaining seconds, remaining fraction, invoked)}.

    GSI only reports remaining time, so a cooldown first seen midway uses its
    observed duration. Subsequent snapshots never restart the sweep.
    """
    with state_lock:
        now = time.monotonic()
        result = {}
        for spell in invoker_hub_overlay.LAYOUT:
            left = max(0.0, ready_at.get(spell, 0) - now)
            total = cooldown_totals.get(spell, left)
            result[spell] = (left, min(1.0, left / total) if total > 0 else 0,
                             spell in invoked)
        return result


def icewall_overlay_state():
    """The hero's (x, y, yaw), or None before GSI has reported it."""
    return hero_minimap_state


def tornado_overlay_state():
    """The last Tornado cast, the orb levels that time it, and which follow-ups are off cooldown."""
    with state_lock:
        now = time.monotonic()
        return {
            "cast": tornado_cast_state,
            "quas_level": ability_levels.get("invoker_quas", 0),
            "wex_level": ability_levels.get("invoker_wex", 0),
            "ready": {spell: now >= ready_at.get(spell, 0) for spell in invoker_tornado_overlay.FOLLOW_UPS},
        }


@app.post("/")
async def gsi(request: Request):
    global invoked, ability_levels, hero_minimap_state
    payload = await request.json()
    abilities = payload.get("abilities", {})
    prev_abilities = payload.get("previously", {}).get("abilities", {})
    with state_lock:
        invoked = {abilities[slot]["name"]: cast_key for slot, cast_key in SLOT_KEYS.items() if slot in abilities}
        ability_levels = {ability["name"]: ability["level"] for ability in abilities.values() if "name" in ability}
        track_cooldown(abilities, prev_abilities)
    for unit in (payload.get("minimap") or {}).values():
        if isinstance(unit, dict) and unit.get("image") == "minimap_herocircle_self":
            hero_minimap_state = (unit["xpos"], unit["ypos"], unit["yaw"])
    return {}


def cast_spell(spell, event_type):

    cast_key = invoked.get(spell)
    if cast_key is None:
        return
    if spell in CAST_IMEDIATELY and event_type == KEY_DOWN:
        keyboard.press_and_release(cast_key)
    elif event_type == KEY_DOWN:
        keyboard.press(cast_key)
    elif event_type == KEY_UP:
        keyboard.release(cast_key)


def run(spell, event_type):
    global tornado_cast_state
    # Ice Wall is aimed while its key is held and cast on release: preview it meanwhile
    if spell == "invoker_ice_wall":
        icewall_overlay.show_requested.emit(event_type == KEY_DOWN)

    # invoke
    if event_type == KEY_DOWN and spell not in invoked:
        for orb in INVOKE_RECIPES[spell]:
            keyboard.press_and_release(KEY_BINDING[orb])

    # wait until invoked
    deadline = time.monotonic() + WAIT_INVOKE_TIMEOUT
    while spell not in invoked and time.monotonic() < deadline:
        time.sleep(0.005)

    if keyboard.is_pressed("alt"):
        return

    # during a Tornado combo, a follow-up pressed before its arc lights up is not cast
    if event_type == KEY_DOWN and not tornado_overlay.is_lit(spell):
        return

    # Tornado is cast on release; remember when and from where for the combo ring
    if (spell == "invoker_tornado" and event_type == KEY_UP and spell in invoked
            and time.monotonic() >= ready_at.get(spell, 0) and hero_minimap_state):
        tornado_cast_state = (time.monotonic(), *hero_minimap_state[:2])

    cast_spell(spell, event_type)


def worker():
    while True:
        run(*event_queue.get())


if __name__ == "__main__":
    # Qt stays on the main thread so the overlays can be toggled at any time;
    # they exist before any key hook or GSI post can reach them.
    qt = QApplication([])
    qt.setQuitOnLastWindowClosed(False)
    camera = utils.Camera()        # one F10 poller, shared by the overlays that need the camera
    hub_overlay = invoker_hub_overlay.InvokerHubOverlay(hub_overlay_state)
    icewall_overlay = invoker_icewall_overlay.InvokerIcewallOverlay(icewall_overlay_state, camera)
    tornado_overlay = invoker_tornado_overlay.InvokerTornadoOverlay(tornado_overlay_state, camera)

    for key, spell in AUTOKEY.items():
        keyboard.hook_key(
            keyboard.key_to_scan_codes(key)[0],
            lambda event, spell=spell: event_queue.put((spell, event.event_type)),
            suppress=True,
        )
    threading.Thread(target=worker, daemon=True).start()
    threading.Thread(target=uvicorn.run, args=(app,),
                     kwargs={"host": "127.0.0.1", "port": 3000, "log_level": "warning"},
                     daemon=True).start()
    signal.signal(signal.SIGINT, lambda *_: qt.quit())
    qt.exec()
