import signal
import threading
import time
import keyboard
from keyboard import KEY_UP, KEY_DOWN
import uvicorn
from fastapi import FastAPI, Request
from pynput.keyboard import Controller, Key
from PySide6.QtWidgets import QApplication

import invoker_hub_overlay
import invoker_icewall_steering
import invoker_tornado_overlay
import utils
from utils import updated_abilities

WAIT_INVOKE_TIMEOUT = 0.2
SLOT_KEYS = {"ability3": "c", "ability4": "v"}   # invoked slot -> cast key

KEY_BINDING = {
    "invoker_quas": "j", "invoker_wex": "k", "invoker_exort": "l", "invoker_invoke": "-",
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
STEER_KEYS = {"f13": "walk", "f14": "left part", "f15": "right part"}   # Synapse: wheel press, tilt left, tilt right

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

invoked = {}                       # spell -> cast key, updated by GSI
ability_levels = {}                # ability name -> level, updated by GSI
hero_position = utils.HeroPosition()   # from GSI's minimap block; yaw 0 = +x, 90 = +y
tornado_cast_state = None          # (release time, hero x, hero y) of the last Tornado cast
ready_at = {}                      # spell -> monotonic time it comes off cooldown
cooldown_totals = {}               # duration observed at the start of each cooldown
state_lock = threading.Lock()
event_queue = utils.DedupeQueue()  # trigger events awaiting the worker
controller = Controller()          # not keyboard.send, which stops keyboard's hooks for every key while sending
hub_overlay = None                 # created on the Qt (main) thread
icewall_steering = None
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
        for spell in AUTOKEY.values():
            left = max(0.0, ready_at.get(spell, 0) - now)
            total = cooldown_totals.get(spell, left)
            result[spell] = (left, min(1.0, left / total) if total > 0 else 0,
                             spell in invoked)
        return result


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
    global invoked, ability_levels
    now = time.monotonic()
    payload = await request.json()
    abilities = payload.get("abilities", {})
    prev_abilities = payload.get("previously", {}).get("abilities", {})
    with state_lock:
        invoked = {abilities[slot]["name"]: cast_key for slot, cast_key in SLOT_KEYS.items() if slot in abilities}
        ability_levels = {ability["name"]: ability["level"] for ability in abilities.values() if "name" in ability}
        track_cooldown(abilities, prev_abilities)
    for unit in (payload.get("minimap") or {}).values():
        if isinstance(unit, dict) and unit.get("image") == "minimap_herocircle_self":
            hero_position.update(now, unit["xpos"], unit["ypos"], unit["yaw"])
    return {}


def cast_spell(spell, event_type):

    cast_key = invoked.get(spell)
    if cast_key is None:
        return
    if spell in CAST_IMEDIATELY and event_type == KEY_DOWN:
        controller.tap(cast_key)
    elif event_type == KEY_DOWN:
        controller.press(cast_key)
    elif event_type == KEY_UP:
        controller.release(cast_key)


def run(spell, event_type):
    global tornado_cast_state

    # invoke
    if event_type == KEY_DOWN and spell not in invoked:
        for orb in INVOKE_RECIPES[spell]:
            controller.tap(KEY_BINDING[orb])

    # wait until invoked
    deadline = time.monotonic() + WAIT_INVOKE_TIMEOUT
    while spell not in invoked and time.monotonic() < deadline:
        time.sleep(0.005)

    if keyboard.is_pressed("alt"):
        return

    # during a Tornado combo, a follow-up pressed before its arc lights up is not cast
    if event_type == KEY_DOWN and not tornado_overlay.is_lit(spell):
        return

    # special case for scepter: left ctrl + Sun Strike sends alt + its cast key, Dota's self-cast (Cataclysm);
    # ctrl is released first, since Dota's ctrl + ability key learns the ability instead
    if spell == "invoker_sun_strike" and keyboard.is_pressed("left ctrl"):
        if event_type == KEY_DOWN and (cast_key := invoked.get(spell)):
            controller.release(Key.ctrl_l)
            with controller.pressed(Key.alt_l):
                controller.tap(cast_key)
        return

    # Tornado is cast on release; remember when and from where for the combo ring
    now = time.monotonic()
    if (spell == "invoker_tornado" and event_type == KEY_UP and spell in invoked and now >= ready_at.get(spell, 0) and (hero := hero_position.get())):
        tornado_cast_state = (now, *hero[:2])

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
    hub_overlay = invoker_hub_overlay.InvokerHubOverlay(hub_overlay_state, AUTOKEY)
    icewall_steering = invoker_icewall_steering.IcewallSteering(STEER_KEYS, hero_position, camera)
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
