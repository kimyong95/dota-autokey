import queue
import threading
import time
import keyboard
import uvicorn
from fastapi import FastAPI, Request
from collections import Counter

WAIT_INVOKE_TIMEOUT = 0.2
SLOT_KEYS = {"ability3": "c", "ability4": "v"}   # invoked slot -> cast key

KEY_BINDING = {
    "invoker_quas": "7", "invoker_wex": "8", "invoker_exort": "9", "invoker_invoke": 12,
}

AUTOKEY = {
    "q": "invoker_cold_snap",   "w": "invoker_forge_spirit", "e": "invoker_alacrity",
    "r": "invoker_sun_strike",  "f": "invoker_ghost_walk",   "d": "invoker_ice_wall",
    "o": "invoker_tornado",     "p": "invoker_emp",          "t": "invoker_sun_strike",
    "4": "invoker_chaos_meteor","5": "invoker_deafening_blast",
}

INVOKE_RECIPES = {
    "invoker_cold_snap":         ["invoker_quas",  "invoker_quas",  "invoker_quas"],
    "invoker_forge_spirit":      ["invoker_quas",  "invoker_exort", "invoker_exort"],
    "invoker_alacrity":          ["invoker_wex",   "invoker_wex",   "invoker_exort"],
    "invoker_sun_strike":        ["invoker_exort", "invoker_exort", "invoker_exort"],
    "invoker_ghost_walk":        ["invoker_quas",  "invoker_quas",  "invoker_wex"],
    "invoker_ice_wall":          ["invoker_quas",  "invoker_quas",  "invoker_exort"],
    "invoker_tornado":           ["invoker_quas",  "invoker_wex",   "invoker_wex"],
    "invoker_emp":               ["invoker_wex",   "invoker_wex",   "invoker_wex"],
    "invoker_chaos_meteor":      ["invoker_wex",   "invoker_exort", "invoker_exort"],
    "invoker_deafening_blast":   ["invoker_quas",  "invoker_wex",   "invoker_exort"],
}

trigger_queue = queue.Queue()      # FIFO of trigger key names pending cast
invoked = {}                       # spell -> cast key, updated by GSI
invoked_event = threading.Event()
current_orbs = []                  # FIFO queue (max 3) of currently active orb bindings

app = FastAPI()


@app.post("/")
async def gsi(request: Request):
    global invoked
    abilities = (await request.json()).get("abilities", {})
    # print(abilities)
    new = {abilities[s]["name"]: k for s, k in SLOT_KEYS.items() if s in abilities}
    if new != invoked:
        invoked = new
        invoked_event.set()
    return {}


def press_orb(orb: str) -> None:
    keyboard.press_and_release(KEY_BINDING[orb])
    current_orbs.append(orb)
    current_orbs[:] = current_orbs[-3:]   # keep only the newest 3 orbs (FIFO)


def incremental_orbs(target: list) -> list:
    for i in range(len(current_orbs) + 1):
        suffix = current_orbs[i:]
        if Counter(suffix) <= Counter(target):
            return list((Counter(target) - Counter(suffix)).elements())


def cast(trigger_key: str) -> None:
    alt = trigger_key.startswith("alt+")
    key = trigger_key[len("alt+"):] if alt else trigger_key
    spell = AUTOKEY[key]
    
    # invoke
    if spell not in invoked:
        for orb in incremental_orbs(INVOKE_RECIPES[spell]):
            press_orb(orb)
        keyboard.press_and_release(KEY_BINDING["invoker_invoke"])

    # wait (up to WAIT_INVOKE_TIMEOUT) for GSI to confirm the spell is invoked
    deadline = time.monotonic() + WAIT_INVOKE_TIMEOUT
    while spell not in invoked and time.monotonic() < deadline:
        time.sleep(0.005)

    # cast (special treatment t for global sun strike)
    if spell in invoked and not alt:
        cast_key = invoked[spell] if key != "t" else f"alt+{invoked[spell]}"
        keyboard.press_and_release(cast_key)


def on_trigger(event):
    if event.event_type == keyboard.KEY_DOWN:
        key = f"alt+{event.name}" if keyboard.is_pressed("alt") else event.name
        trigger_queue.put(key)


def worker():
    while True:
        trigger_key = trigger_queue.get()
        cast(trigger_key)

if __name__ == "__main__":
    for trigger_key in AUTOKEY:
        keyboard.hook_key(trigger_key, on_trigger, suppress=True)
    threading.Thread(target=worker, daemon=True).start()
    uvicorn.run(app, host="127.0.0.1", port=3000, log_level="warning")