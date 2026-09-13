import threading
import time
import keyboard
from keyboard import KEY_UP, KEY_DOWN
import uvicorn
from fastapi import FastAPI, Request

import invoker_overlay
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
cooldown_totals = {}               # duration observed at the start of each cooldown
state_lock = threading.Lock()
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
        cooldown_totals.clear()
    for ability in updated.values():
        spell, left = ability["name"], ability["cooldown"]
        previous = max(0.0, ready_at.get(spell, 0) - now)
        if left <= 0:
            cooldown_totals.pop(spell, None)
        elif spell not in cooldown_totals or left > previous + 0.5:
            cooldown_totals[spell] = left
        ready_at[spell] = now + left

def castable(spell):
    return time.monotonic() >= ready_at.get(spell, 0)   # unseen spell -> assume up


def overlay_state():
    """One consistent snapshot: {spell: (remaining seconds, remaining fraction, invoked)}.

    GSI only reports remaining time, so a cooldown first seen midway uses its
    observed duration. Subsequent snapshots never restart the sweep.
    """
    with state_lock:
        now = time.monotonic()
        result = {}
        for spell in invoker_overlay.LAYOUT:
            left = max(0.0, ready_at.get(spell, 0) - now)
            total = cooldown_totals.get(spell, left)
            result[spell] = (left, min(1.0, left / total) if total > 0 else 0,
                             spell in invoked)
        return result


@app.post("/")
async def gsi(request: Request):
    global invoked
    payload = await request.json()
    abilities = payload.get("abilities", {})
    prev_abilities = payload.get("previously", {}).get("abilities", {})
    with state_lock:
        invoked = {abilities[slot]["name"]: cast_key for slot, cast_key in SLOT_KEYS.items() if slot in abilities}
        track_cooldown(abilities, prev_abilities)
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


def worker():
    while True:
        run(*event_queue.get())


if __name__ == "__main__":
    for key in AUTOKEY:
        keyboard.hook_key(
            keyboard.key_to_scan_codes(key)[0],
            lambda event, key=key: event_queue.put((key, event.event_type)),
            suppress=True,
        )
    threading.Thread(target=worker, daemon=True).start()

    # Qt stays on the main thread so can toggle the overlay at any time.
    threading.Thread(target=uvicorn.run, args=(app,),
                     kwargs={"host": "127.0.0.1", "port": 3000, "log_level": "warning"},
                     daemon=True).start()
    invoker_overlay.start(overlay_state)
