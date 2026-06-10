import threading
import time
import keyboard
import uvicorn
from fastapi import FastAPI, Request

INTERVAL = 0.5
INVOKE_TIMEOUT = 0.2
SLOT_KEYS = {"ability3": "c", "ability4": "v"}   # invoked slot -> cast key

KEY_BINDING = {
    "invoker_quas": "7", "invoker_wex": "8", "invoker_exort": "9", "invoker_invoke": 12,
}

AUTOKEY = {
    "q": "invoker_cold_snap",   "w": "invoker_forge_spirit", "e": "invoker_alacrity",
    "r": "invoker_sun_strike",  "d": "invoker_ghost_walk",   "f": "invoker_ice_wall",
    "o": "invoker_tornado",     "p": "invoker_emp",
    "4": "invoker_chaos_meteor","5": "invoker_deafening_blast",
}

INVOKE_RECIPES = {
    "invoker_cold_snap":       ["invoker_quas",  "invoker_quas",  "invoker_quas",  "invoker_invoke"],
    "invoker_forge_spirit":    ["invoker_quas",  "invoker_exort", "invoker_exort", "invoker_invoke"],
    "invoker_alacrity":        ["invoker_wex",   "invoker_wex",   "invoker_exort", "invoker_invoke"],
    "invoker_sun_strike":      ["invoker_exort", "invoker_exort", "invoker_exort", "invoker_invoke"],
    "invoker_ghost_walk":      ["invoker_quas",  "invoker_quas",  "invoker_wex",   "invoker_invoke"],
    "invoker_ice_wall":        ["invoker_quas",  "invoker_quas",  "invoker_exort", "invoker_invoke"],
    "invoker_tornado":         ["invoker_quas",  "invoker_wex",   "invoker_wex",   "invoker_invoke"],
    "invoker_emp":             ["invoker_wex",   "invoker_wex",   "invoker_wex",   "invoker_invoke"],
    "invoker_chaos_meteor":    ["invoker_wex",   "invoker_exort", "invoker_exort", "invoker_invoke"],
    "invoker_deafening_blast": ["invoker_quas",  "invoker_wex",   "invoker_exort", "invoker_invoke"],
}

active_trigger = None
wake = threading.Event()
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


def cast(spell: str) -> None:
    # if not invoked
    if spell not in invoked:
        for binding in INVOKE_RECIPES[spell]:
            keyboard.press_and_release(KEY_BINDING[binding])
        end = time.monotonic() + INVOKE_TIMEOUT
    
    # wait for invoke to register, or timeout
    while spell not in invoked:
        invoked_event.clear()
        if spell in invoked or not invoked_event.wait(end - time.monotonic()):
            break
    
    key = invoked.get(spell)
    if key and not keyboard.is_pressed("alt"):
        keyboard.press_and_release(invoked.get(spell))

def on_trigger(event):
    global active_trigger
    if event.event_type == keyboard.KEY_DOWN:
        active_trigger = event.name
        wake.set()


def worker():
    last_fire = 0.0
    while True:
        wake.wait()
        wake.clear()
        wait_left = INTERVAL - (time.monotonic() - last_fire)
        if wait_left > 0:
            time.sleep(wait_left)

        cast(AUTOKEY[active_trigger])
        last_fire = time.monotonic()

if __name__ == "__main__":
    for trigger_key in AUTOKEY:
        keyboard.hook_key(trigger_key, on_trigger, suppress=True)
    threading.Thread(target=worker, daemon=True).start()
    uvicorn.run(app, host="127.0.0.1", port=3000, log_level="warning")