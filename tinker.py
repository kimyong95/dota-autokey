import threading

import keyboard
import uvicorn
from fastapi import FastAPI, Request

LOOP_INTERVAL = 0.03

KEY_BINDING = {
    "tinker_laser":          "q",
    "tinker_warp_grenade":   "d",
    "tinker_deploy_turrets": "e",
    "tinker_rearm":          "f",
}

AUTOKEY = {
    "[": ["tinker_warp_grenade", "tinker_laser", "tinker_rearm"],
    "]": ["tinker_warp_grenade", "tinker_laser", "tinker_deploy_turrets", "tinker_rearm"],
}

TRIGGER_KEYS = set(AUTOKEY.keys())
IGNORE_KEYS  = {
    t: TRIGGER_KEYS | {KEY_BINDING[s] for s in spells}
    for t, spells in AUTOKEY.items()
}

active_trigger = None
suppress_rearm = False

castable = {
    skill: True
    for abilities in AUTOKEY.values()
    for skill in abilities
}

trigger_events = {key: threading.Event() for key in AUTOKEY}
gsi_events    = {key: threading.Event() for key in AUTOKEY}

app = FastAPI()


@app.post("/")
async def gsi(request: Request):
    payload = await request.json()
    abilities = payload.get("abilities", {})
    for ability_id, prev_ability_data in payload.get("previously", {}).get("abilities", {}).items():
        curr_ability_data = abilities[ability_id]
        curr_ability_name = curr_ability_data["name"]
        if "can_cast" in prev_ability_data and curr_ability_name in castable:
            castable[curr_ability_name] = curr_ability_data["can_cast"]
            for ev in gsi_events.values():
                ev.set()
    return {}


def _make_on_trigger(trigger_key: str, event_obj: threading.Event):
    def _on_trigger(event) -> None:
        global active_trigger, suppress_rearm
        if event.event_type == keyboard.KEY_DOWN and active_trigger is None:
            active_trigger = trigger_key
            suppress_rearm = False
            event_obj.set()
        elif event.event_type == keyboard.KEY_UP and active_trigger == trigger_key:
            active_trigger = None
            event_obj.clear()
    return _on_trigger

def _on_any_key(event):
    global suppress_rearm
    if event.event_type == keyboard.KEY_DOWN and active_trigger is not None and event.name not in IGNORE_KEYS[active_trigger]:
        suppress_rearm = True

def autokey_worker(abilities: list, key_event: threading.Event, gsi_event: threading.Event) -> None:
    while True:
        key_event.wait()
        gsi_event.wait(timeout=LOOP_INTERVAL)

        to_fire = next((a for a in abilities if castable.get(a)), abilities[0])
        if suppress_rearm and to_fire == "tinker_rearm":
            continue
        keyboard.press_and_release(KEY_BINDING[to_fire])
        keyboard.press_and_release("2")

        gsi_event.clear()


if __name__ == "__main__":

    keyboard.hook(_on_any_key)

    for trigger_key, abilities in AUTOKEY.items():
        key_ev = trigger_events[trigger_key]
        keyboard.hook_key(trigger_key, _make_on_trigger(trigger_key, key_ev), suppress=True)
        threading.Thread(
            target=autokey_worker,
            args=(abilities, key_ev, gsi_events[trigger_key]),
            daemon=True,
        ).start()
    uvicorn.run(app, host="127.0.0.1", port=3000, log_level="warning")
