import signal
import threading
import keyboard
from pathlib import Path
from pynput import keyboard as pk
import time
from PySide6.QtWidgets import QApplication
from info_overlay import InfoOverlay
from tinker_ability_monitor import TinkerAbilityMonitor
from tinker_repeat_key import REPEATABLE, TinkerRepeatKey
from utils import key_name, read_yaml_list, toggle_yaml_list

LOOP_INTERVAL = 0.03
CONFIG = Path(__file__).with_name("tinker.yaml")    # combo, extra_keys, and TinkerRepeatKey's repeat_keys

controller = pk.Controller()

KEY_BINDING = {
    "tinker_laser":                 "q",
    "tinker_march_of_the_machines": "w",
    "tinker_warp_grenade":          "d",
    "tinker_deploy_turrets":        "e",
    "tinker_rearm":                 "f",
}
AUTOKEY = "o"
HERO_ONLY_KEY = "h"                 # Dota: while held, clicks and casts interact only with heroes; held with AUTOKEY
COMBO_ORDER = ["tinker_deploy_turrets", "tinker_warp_grenade", "tinker_laser", "tinker_march_of_the_machines", "tinker_rearm"]
EXTRA_KEY_CHOICES = ["2", "3", "x", "6"]    # items: pressed every loop, not monitored
ALL_KEYS = [*KEY_BINDING.values(), *EXTRA_KEY_CHOICES, *REPEATABLE]
assert len(set(ALL_KEYS)) == len(ALL_KEYS), "combo, extra and repeat keys overlap"

monitor = None                      # TinkerAbilityMonitor: the ability states off the screen
info = None                         # InfoOverlay: one line over Dota
combo = []                          # hotkeys of the abilities in the combo; Alt + one toggles it
extra_keys = []                     # Alt + one of EXTRA_KEY_CHOICES toggles it
held = threading.Event()            # AUTOKEY is down
suppress_rearm = False

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


def announce(name, keys):
    """Print a list after a toggle and show it over Dota: e.g. "combo: E D Q F"."""
    text = f"{name}: {' '.join(keys).upper() or 'none'}"
    print(text)
    info.show_line(text)


def on_press(key, injected):
    global combo, extra_keys, suppress_rearm
    if injected:                     # one of our own sent keys -> not a real interrupt
        return
    name = key_name(key)
    if keyboard.is_pressed("alt") and name in KEY_BINDING.values():
        combo = toggle_yaml_list(CONFIG, "combo", name)
        announce("combo", [KEY_BINDING[a] for a in COMBO_ORDER if KEY_BINDING[a] in combo])
    elif keyboard.is_pressed("alt") and name in EXTRA_KEY_CHOICES:
        extra_keys = toggle_yaml_list(CONFIG, "extra_keys", name)
        announce("extra", extra_keys)
    elif held.is_set() and name != AUTOKEY:
        suppress_rearm = True         # a key of the user's own while the autokey runs, not its auto-repeat

def worker():
    while True:
        held.wait()
        monitor.activate()            # keeps it watching while AUTOKEY is held; all castable until its first frame
        states = monitor.states
        abilities = [a for a in COMBO_ORDER if KEY_BINDING[a] in combo]
        castable = [a for a in abilities if states[a] == "castable"]    # casting counts as not castable
        to_fire = castable[0] if castable else abilities[0] if abilities else None   # none castable: the first
        if to_fire and not (suppress_rearm and to_fire == "tinker_rearm"):
            press_and_release(KEY_BINDING[to_fire])
        for k in extra_keys:
            press_and_release(k)
        time.sleep(LOOP_INTERVAL)


if __name__ == "__main__":
    qt = QApplication([])           # first: the overlay needs it, and it makes the process DPI aware
    qt.setQuitOnLastWindowClosed(False)
    info = InfoOverlay()
    combo = [key for key in read_yaml_list(CONFIG, "combo") if key in KEY_BINDING.values()]
    extra_keys = [key for key in read_yaml_list(CONFIG, "extra_keys") if key in EXTRA_KEY_CHOICES]
    monitor = TinkerAbilityMonitor()
    repeat_key = TinkerRepeatKey(CONFIG, announce)
    keyboard.hook_key(AUTOKEY, on_trigger, suppress=True)
    threading.Thread(target=worker, daemon=True).start()
    listener = pk.Listener(on_press=on_press)
    listener.start()
    startup = (f"combo: {' '.join(KEY_BINDING[a] for a in COMBO_ORDER if KEY_BINDING[a] in combo).upper()}"
               f"  extra: {' '.join(extra_keys).upper()}  repeat: {' '.join(repeat_key.keys).upper()}")
    print(startup)
    info.show_line(startup)
    signal.signal(signal.SIGINT, lambda *_: qt.quit())
    qt.exec()
