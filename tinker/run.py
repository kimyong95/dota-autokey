import threading
import keyboard
import uvicorn
from fastapi import FastAPI, Request
from pynput import keyboard as pk
import time
from tinker.ability_monitor import TinkerAbilityMonitor
from tinker.repeat_key import TinkerRepeatKey
from utils.keyboard import key_name

LOOP_INTERVAL = 0.03

controller = pk.Controller()

COMBO = {                           # ability -> hotkey, in casting order: each loop casts the first castable one
    "tinker_warp_grenade":   "d",
    "tinker_deploy_turrets": "e",
    "tinker_laser":          "q",
    "tinker_rearm":          "f",
}
AUTOKEY = "o"
HERO_ONLY_KEY = "h"                 # Dota: while held, clicks and casts interact only with heroes; held with AUTOKEY
SLOT_KEYS = {"slot0": "1", "slot1": "2", "slot2": "3", "slot3": "z", "slot4": "x", "slot5": "6"}  # GSI inventory slot -> Dota hotkey; slot6-8 are the backpack
REPEAT_ITEMS = {"item_blink", "item_overwhelming_blink", "item_swift_blink", "item_arcane_blink","item_cyclone", "item_wind_waker"}
EXTRA_ITEMS = {"item_sheepstick", "item_dagon", "item_dagon_2", "item_dagon_3", "item_dagon_4", "item_dagon_5", "item_ethereal_blade", "item_ghost", "item_soul_ring", "item_orchid", "item_bloodthorn"}         # pressed every loop
assert not set(COMBO.values()) & set(SLOT_KEYS.values()), "combo and item keys overlap"

monitor = None                      # TinkerAbilityMonitor: the ability states off the screen
repeat_key = None                   # TinkerRepeatKey: GSI sets its keys
extra_keys = []                     # slot keys of the EXTRA_ITEMS in the inventory, set by GSI
held = threading.Event()            # AUTOKEY is down
suppress_rearm = False

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


@app.post("/")
async def gsi(request: Request):
    global extra_keys
    items = (await request.json()).get("items")
    if items:
        names = {key: items.get(slot, {}).get("name") for slot, key in SLOT_KEYS.items()}
        repeat = [key for key, name in names.items() if name in REPEAT_ITEMS]
        extra = [key for key, name in names.items() if name in EXTRA_ITEMS]
        repeat_key.keys, extra_keys = repeat, extra
    return {}


def worker():
    while True:
        held.wait()
        monitor.activate()            # keeps it watching while AUTOKEY is held; all castable until its first frame
        states = monitor.states
        castable = [a for a in COMBO if states[a] == "castable"]     # casting counts as not castable
        to_fire = castable[0] if castable else next(iter(COMBO))      # none castable: the first
        if not (suppress_rearm and to_fire == "tinker_rearm"):
            press_and_release(COMBO[to_fire])
        for k in extra_keys:
            press_and_release(k)
        time.sleep(LOOP_INTERVAL)


if __name__ == "__main__":
    monitor = TinkerAbilityMonitor()  # first: it makes the process DPI aware
    repeat_key = TinkerRepeatKey()
    keyboard.hook_key(AUTOKEY, on_trigger, suppress=True)
    threading.Thread(target=worker, daemon=True).start()
    pk.Listener(on_press=on_press).start()
    uvicorn.run(app, host="127.0.0.1", port=3000, log_level="warning")     # GSI posts; Ctrl + C quits
