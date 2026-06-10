import threading
import keyboard
import uvicorn
from fastapi import FastAPI, Request
from pynput import keyboard as pk
import time
import threading

LOOP_INTERVAL = 0.03

controller = pk.Controller() 

KEY_BINDING = {
    "tinker_laser":          "q",
    "tinker_warp_grenade":   "d",
    "tinker_deploy_turrets": "e",
    "tinker_rearm":          "f",
}
AUTOKEY = {
    "o": ["tinker_warp_grenade", "tinker_laser", "tinker_rearm"],
    "p": ["tinker_warp_grenade", "tinker_laser", "tinker_deploy_turrets", "tinker_rearm"],
}
EXTRA_KEYS = ["2", "6"]

IGNORE_KEYS = {
    t: set(AUTOKEY) | {KEY_BINDING[s] for s in spells} | set(EXTRA_KEYS)
    for t, spells in AUTOKEY.items()
}

castable = {a: True for combo in AUTOKEY.values() for a in combo}

active_trigger = None
suppress_rearm = False
wake = threading.Event()
app = FastAPI()

def press_and_release(key):
    controller.press(key)
    controller.release(key)

@app.post("/")
async def gsi(request: Request):
    payload = await request.json()
    abilities = payload.get("abilities", {})
    prev_abilities = payload.get("previously", {}).get("abilities", {})
    if not isinstance(prev_abilities, dict):
        return {}
    
    for aid, prev in prev_abilities.items():
        name = abilities[aid]["name"]
        if "can_cast" in prev and name in castable:
            castable[name] = abilities[aid]["can_cast"]
            wake.set()                   # wake immediately on GSI update
    return {}


def on_trigger(event):
    global active_trigger, suppress_rearm
    if event.event_type == keyboard.KEY_DOWN and active_trigger is None:
        active_trigger = event.name
        suppress_rearm = False
        wake.set()                       # wake immediately on key press
    elif event.event_type == keyboard.KEY_UP and active_trigger == event.name:
        active_trigger = None


def on_interrupt(key, injected):
    global suppress_rearm
    if injected:                     # one of our own sent keys -> not a real interrupt
        return
    if active_trigger is None:       # only relevant while an autokey is active
        return
    if isinstance(key, pk.KeyCode) and key.char.lower() in AUTOKEY:
        return                        # ignore other autokeys
    suppress_rearm = True

def worker():
    last_fire = 0.0
    while True:
        trigger = active_trigger          # single read, top of every iteration
        if trigger is None:
            wake.wait()
            wake.clear()
            continue

        wait_left = LOOP_INTERVAL - (time.monotonic() - last_fire)
        if wait_left > 0:
            wake.wait(timeout=wait_left)
            continue                      # loop back → re-reads trigger fresh

        combo = AUTOKEY[trigger]          # trigger still current: no wait since read
        to_fire = next((a for a in combo if castable.get(a)), combo[0])
        if not (suppress_rearm and to_fire == "tinker_rearm"):
            press_and_release(KEY_BINDING[to_fire])
        for k in EXTRA_KEYS:
            press_and_release(k)
        last_fire = time.monotonic()


if __name__ == "__main__":
    for trigger_key in AUTOKEY:
        keyboard.hook_key(trigger_key, on_trigger, suppress=True)
    threading.Thread(target=worker, daemon=True).start()
    listener = pk.Listener(on_press=on_interrupt)
    listener.start()
    uvicorn.run(app, host="127.0.0.1", port=3000, log_level="warning")