import argparse
import threading
import time
import keyboard
from keyboard import KEY_UP, KEY_DOWN
import uvicorn
from fastapi import FastAPI, Request

import invoker_overlay
from utils import updated_abilities

WAIT_INVOKE_TIMEOUT = 0.2
WAIT_CAST_TIMEOUT = 0.2
SLOT_KEYS = {"ability3": "c", "ability4": "v"}   # invoked slot -> cast key

TORNADO_TRAVEL_SPEED = 1000     # units/s, flat
TORNADO_AIRTIME_MULTIPLIER = 0.2

DELAY_TIME = {                  # spell -> seconds between cast and impact
    "invoker_sun_strike": 1.7,
    "invoker_chaos_meteor": 1.3,
}

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
    "7": ["invoker_cold_snap", "invoker_emp", "invoker_ice_wall", "invoker_chaos_meteor", "invoker_deafening_blast", "invoker_sun_strike"],
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
ready_at = {}                      # spell -> monotonic time it comes off cooldown
tornado_air_time = 0.0             # how long this tornado holds its victim up
tornado_max_land_time = 0.0        # monotonic time a max-range tornado's victim lands
event_queue = DedupeQueue()        # trigger events awaiting the worker

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
    for ability in updated.values():
        ready_at[ability["name"]] = now + ability["cooldown"]

def castable(spell):
    return time.monotonic() >= ready_at.get(spell, 0)   # unseen spell -> assume up


def remaining(spell):
    return max(0.0, ready_at.get(spell, 0) - time.monotonic())   # unseen spell -> ready


def tornado_times(abilities):
    # travel distance scales with Wex, the lift ("air time") with Quas
    levels = {ability["name"]: ability["level"] for ability in abilities.values()}
    quas, wex = levels.get("invoker_quas", 0), levels.get("invoker_wex", 0)
    if not quas or not wex:
        return 0.0, 0.0
    travel_time = (1500 + 300 * (wex - 1)) / TORNADO_TRAVEL_SPEED
    air_time = 1.2 + TORNADO_AIRTIME_MULTIPLIER * (quas - 1)

    return travel_time, air_time


def track_tornado_land_time(abilities, prev_abilities):
    # can_cast is a bool, so "changed this tick and is now False" is the True -> False edge
    global tornado_air_time, tornado_max_land_time
    for ability in updated_abilities(abilities, prev_abilities, "can_cast").values():
        if ability["name"] == "invoker_tornado" and not ability["can_cast"]:
            travel_time, tornado_air_time = tornado_times(abilities)
            tornado_max_land_time = time.monotonic() + travel_time + tornado_air_time


@app.post("/")
async def gsi(request: Request):
    global invoked
    payload = await request.json()
    abilities = payload.get("abilities", {})
    prev_abilities = payload.get("previously", {}).get("abilities", {})
    invoked = {abilities[slot]["name"]: cast_key for slot, cast_key in SLOT_KEYS.items() if slot in abilities}
    track_cooldown(abilities, prev_abilities)
    track_tornado_land_time(abilities, prev_abilities)
    return {}

def get_spell(key):
    spell = AUTOKEY[key]
    if isinstance(spell, list):
        spell = next((s for s in spell if castable(s)), spell[0])
    return spell


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


def run(key, event_type):
    spell = get_spell(key)

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

    cast_spell(spell, event_type)


def on_trigger(event):
    # hand off immediately; the hook must not block
    event_queue.put((event.name, event.event_type))


def worker():
    while True:
        run(*event_queue.get())


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--overlay", action="store_true", help="show the cooldown overlay")
    args = parser.parse_args()

    for trigger_key in AUTOKEY:
        keyboard.hook_key(trigger_key, on_trigger, suppress=True)
    threading.Thread(target=worker, daemon=True).start()

    if not args.overlay:
        uvicorn.run(app, host="127.0.0.1", port=3000, log_level="warning")
    else:
        # Qt needs the main thread, so uvicorn moves off it
        threading.Thread(target=uvicorn.run, args=(app,),
                         kwargs={"host": "127.0.0.1", "port": 3000, "log_level": "warning"},
                         daemon=True).start()
        invoker_overlay.start(remaining)