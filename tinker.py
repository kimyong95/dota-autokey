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
    "o": ["tinker_warp_grenade", "tinker_laser", "tinker_rearm"],
    "p": ["tinker_warp_grenade", "tinker_deploy_turrets", "tinker_rearm"],
}

castable = {
    skill: False
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


def _make_on_trigger(event_obj: threading.Event):
    def _on_trigger(event) -> None:
        if event.event_type == keyboard.KEY_DOWN:
            event_obj.set()
        else:
            event_obj.clear()
    return _on_trigger


def autokey_worker(abilities: list, key_event: threading.Event, gsi_event: threading.Event) -> None:
    while True:
        key_event.wait()
        gsi_event.wait(timeout=LOOP_INTERVAL)

        to_fire = next((a for a in abilities if castable.get(a)), abilities[0])
        keyboard.press_and_release(KEY_BINDING[to_fire])

        gsi_event.clear()


if __name__ == "__main__":
    for trigger_key, abilities in AUTOKEY.items():
        key_ev = trigger_events[trigger_key]
        keyboard.hook_key(trigger_key, _make_on_trigger(key_ev), suppress=True)
        threading.Thread(
            target=autokey_worker,
            args=(abilities, key_ev, gsi_events[trigger_key]),
            daemon=True,
        ).start()
    uvicorn.run(app, host="127.0.0.1", port=3000, log_level="warning")
