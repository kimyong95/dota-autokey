import threading
import time
import keyboard
from keyboard import KEY_UP, KEY_DOWN
import uvicorn
from fastapi import FastAPI, Request

WAIT_INVOKE_TIMEOUT = 0.2
SLOT_KEYS = {"ability3": "c", "ability4": "v"}   # invoked slot -> cast key

KEY_BINDING = {
    "invoker_quas": "7", "invoker_wex": "8", "invoker_exort": "9", "invoker_invoke": 12,
}

AUTOKEY = {
    "o": "invoker_cold_snap",   "d": "invoker_forge_spirit", "f": "invoker_alacrity",
    "e": "invoker_sun_strike",  "q": "invoker_emp",   "4": "invoker_ghost_walk",
    "5": "invoker_tornado",     "w": "invoker_ice_wall",
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
event_queue = DedupeQueue()        # trigger events awaiting the worker

app = FastAPI()


@app.post("/")
async def gsi(request: Request):
    global invoked
    abilities = (await request.json()).get("abilities", {})
    invoked = {abilities[s]["name"]: k for s, k in SLOT_KEYS.items() if s in abilities}
    return {}


def run(key, event_type):
    spell = AUTOKEY[key]

    # invoke
    if event_type == KEY_DOWN and spell not in invoked:
        for orb in INVOKE_RECIPES[spell]:
            keyboard.press_and_release(KEY_BINDING[orb])
        deadline = time.monotonic() + WAIT_INVOKE_TIMEOUT
        while spell not in invoked and time.monotonic() < deadline:
            time.sleep(0.005)

    if keyboard.is_pressed("alt"):
        return

    # cast
    cast_key = invoked.get(spell)
    if cast_key:
        if event_type == KEY_DOWN:
            keyboard.press(cast_key)
        elif event_type == KEY_UP:
            keyboard.release(cast_key)


def on_trigger(event):
    # hand off immediately; the hook must not block
    event_queue.put((event.name, event.event_type))


def worker():
    while True:
        run(*event_queue.get())


if __name__ == "__main__":
    for trigger_key in AUTOKEY:
        keyboard.hook_key(trigger_key, on_trigger, suppress=True)
    threading.Thread(target=worker, daemon=True).start()
    uvicorn.run(app, host="127.0.0.1", port=3000, log_level="warning")