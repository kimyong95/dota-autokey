import queue
import threading
import time
import keyboard
from keyboard import KEY_DOWN
from pynput import mouse as pm

EUL_TRIGGER = "o"              # our own hotkeys; the game never sees these
ULTI_TRIGGER = "p"

EUL_KEY = "z"                  # item slot holding Eul's Scepter of Divinity
ULTI_KEY = "r"                 # nevermore_requiem
RAZE_KEYS = ["q", "w", "e"]    # nevermore_shadowraze, close / medium / far
TURN_KEY = "m"                 # what a held raze key becomes
STOP_KEY = "s"

EUL_AIR_TIME = 2.5             # cyclone duration
ULTI_CAST_TIME = 1.67          # Requiem's cast point
OFFSET = 0.0

mouse = pm.Controller()

eul_land_time = 0.0            # monotonic time the cycloned victim comes down
eul_pressed = threading.Event()
ulti_pressed = threading.Event()
raze_queue = queue.Queue()


def on_eul(event):
    # hand off immediately; the hook must not block. the clock starts here rather than in
    # the worker, so an ulti trigger right behind this one always sees the new landing time
    global eul_land_time
    if event.event_type == KEY_DOWN:
        eul_land_time = time.monotonic() + EUL_AIR_TIME
        eul_pressed.set()


def on_ulti(event):
    # hand off immediately; the hook must not block
    if event.event_type == KEY_DOWN:
        ulti_pressed.set()


def on_raze(event):
    # hand off immediately; the hook must not block
    raze_queue.put((event.name, event.event_type))


def eul_worker():
    while True:
        eul_pressed.wait()
        eul_pressed.clear()
        keyboard.press_and_release(EUL_KEY)


def ulti_worker():
    while True:
        ulti_pressed.wait()
        ulti_pressed.clear()
        # start the cast so it finishes as the victim lands; a missing or stale eul puts
        # that landing in the past, which is what casts the ult right away
        wait = eul_land_time - ULTI_CAST_TIME - time.monotonic() - OFFSET
        if wait > 0:
            keyboard.press_and_release(STOP_KEY)
            time.sleep(wait)
        keyboard.press_and_release(ULTI_KEY)


def raze_worker():
    # a held raze key stands in for the turn key, so the right click swings the hero round
    # while you hold; dota casts on release, so the raze goes off already facing the cursor
    while True:
        raze_key, event_type = raze_queue.get()
        if event_type == KEY_DOWN:
            keyboard.press(TURN_KEY)
            keyboard.press(raze_key)
            mouse.press(pm.Button.right)
            mouse.release(pm.Button.right)
        else:
            keyboard.release(TURN_KEY)
            keyboard.release(raze_key)


if __name__ == "__main__":
    keyboard.hook_key(EUL_TRIGGER, on_eul, suppress=True)
    keyboard.hook_key(ULTI_TRIGGER, on_ulti, suppress=True)
    for raze_key in RAZE_KEYS:
        keyboard.hook_key(raze_key, on_raze, suppress=True)
    threading.Thread(target=eul_worker, daemon=True).start()
    threading.Thread(target=ulti_worker, daemon=True).start()
    threading.Thread(target=raze_worker, daemon=True).start()
    keyboard.wait()
