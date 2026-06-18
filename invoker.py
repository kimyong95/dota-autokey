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

GLOBAL_SUN_STRIKE = "e"

AUTOKEY = {
    "o": "invoker_cold_snap",   "d": "invoker_forge_spirit", "f": "invoker_alacrity",
    "w": "invoker_sun_strike",  "7": "invoker_ghost_walk",   "5": "invoker_ice_wall",
    "4": "invoker_tornado",     "q": "invoker_emp",          "e": "invoker_sun_strike",
    "r": "invoker_chaos_meteor","p": "invoker_deafening_blast",
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

trigger_queue = queue.Queue()      # FIFO of trigger key names pending cast
invoked = {}                       # spell -> cast key, updated by GSI
invoked_event = threading.Event()

app = FastAPI()


@app.post("/")
async def gsi(request: Request):
    global invoked
    abilities = (await request.json()).get("abilities", {})
    new = {abilities[s]["name"]: k for s, k in SLOT_KEYS.items() if s in abilities}
    if new != invoked:
        invoked = new
        invoked_event.set()
    return {}


def cast(trigger_key: str) -> None:
    alt = trigger_key.startswith("alt+")
    key = trigger_key[len("alt+"):] if alt else trigger_key
    spell = AUTOKEY[key]
    
    # invoke
    if spell not in invoked:
        for orb in INVOKE_RECIPES[spell]:
            keyboard.press_and_release(KEY_BINDING[orb])

    # wait (up to WAIT_INVOKE_TIMEOUT) for GSI to confirm the spell is invoked
    deadline = time.monotonic() + WAIT_INVOKE_TIMEOUT
    while spell not in invoked and time.monotonic() < deadline:
        time.sleep(0.005)
    
    # cast (special treatment t for global sun strike)
    if spell in invoked and not alt:
        cast_key = invoked[spell] if key != GLOBAL_SUN_STRIKE else f"alt+{invoked[spell]}"
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