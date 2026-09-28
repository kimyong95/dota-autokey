import threading
import keyboard
from pynput import keyboard as pk
import time
from tinker_ability_monitor import TinkerAbilityMonitor

LOOP_INTERVAL = 0.03

controller = pk.Controller()

KEY_BINDING = {
    "tinker_laser":          "q",
    "tinker_warp_grenade":   "d",
    "tinker_deploy_turrets": "e",
    "tinker_rearm":          "f",
}
AUTOKEY = "f13"
COMBO = ["tinker_warp_grenade", "tinker_laser", "tinker_rearm"]
EXTRA_KEYS = ["2", "6"]

monitor = None                      # TinkerAbilityMonitor: the ability states off the screen
held = threading.Event()            # AUTOKEY is down
suppress_rearm = False

def press_and_release(key):
    controller.press(key)
    controller.release(key)


def on_trigger(event):
    global suppress_rearm
    if event.event_type == keyboard.KEY_UP:
        held.clear()
    elif not held.is_set():         # a fresh press, not auto-repeat
        suppress_rearm = False
        held.set()


def on_interrupt(key, injected):
    global suppress_rearm
    if injected:                     # one of our own sent keys -> not a real interrupt
        return
    if not held.is_set():            # only relevant while the autokey is active
        return
    if key == pk.Key[AUTOKEY]:
        return                        # the autokey's own auto-repeat
    suppress_rearm = True

def worker():
    while True:
        held.wait()
        monitor.activate()            # keeps it watching while AUTOKEY is held; all castable until its first frame
        states = monitor.states
        # casting counts as not castable, so the next ability in the combo fires
        to_fire = next((a for a in COMBO if states[a] == "castable"), COMBO[0])
        if not (suppress_rearm and to_fire == "tinker_rearm"):
            press_and_release(KEY_BINDING[to_fire])
        for k in EXTRA_KEYS:
            press_and_release(k)
        time.sleep(LOOP_INTERVAL)


if __name__ == "__main__":
    monitor = TinkerAbilityMonitor()
    keyboard.hook_key(AUTOKEY, on_trigger, suppress=True)
    threading.Thread(target=worker, daemon=True).start()
    listener = pk.Listener(on_press=on_interrupt)
    listener.start()
    try:
        keyboard.wait()
    except KeyboardInterrupt:
        pass
