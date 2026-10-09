"""Repeat keys: while one of `keys` is held, keep sending its keydown (never a keyup) every REPEAT_INTERVAL.

    repeat_key = TinkerRepeatKey()
    repeat_key.keys = {"1", "z"}        # the keys to repeat; set them from any thread, any time

A key's keydown starts the repeat, whatever modifiers are held: Alt + 1 repeats 1, and Dota sees Alt + 1 each
time. Any other key event stops it: the key's release, or another key going down or up. Modifier events do not,
and neither do injected ones: the repeats themselves, and other autokeys' presses. It hooks with pynput, low
level and never suppressing, so Dota sees every key too.
"""
import threading
import time

from pynput import keyboard as pk

from utils.keyboard import key_name

REPEAT_INTERVAL = 0.03
MODIFIERS = {pk.Key.alt, pk.Key.alt_l, pk.Key.alt_r, pk.Key.alt_gr, pk.Key.ctrl, pk.Key.ctrl_l, pk.Key.ctrl_r, pk.Key.shift, pk.Key.shift_l, pk.Key.shift_r, pk.Key.cmd, pk.Key.cmd_l, pk.Key.cmd_r}

controller = pk.Controller()


class TinkerRepeatKey:
    """Create once; `keys` are the keys to repeat, none at first."""

    def __init__(self):
        self.keys = set()
        self.repeating = None           # the key of `keys` held down, while its repeat runs
        self.wake = threading.Event()
        pk.Listener(on_press=self.on_press, on_release=self.on_release).start()
        threading.Thread(target=self.run, daemon=True).start()

    def on_press(self, key, injected):
        if injected or key in MODIFIERS:
            return
        name = key_name(key)
        if name is not None and name == self.repeating:     # the held key's own auto-repeat
            return
        self.repeating = None                               # any other key down stops the repeat
        if name in self.keys:
            self.repeating = name
            self.wake.set()

    def on_release(self, key, injected):
        if not injected and key not in MODIFIERS:
            self.repeating = None       # the key's release, or any other key up, stops the repeat

    def run(self):
        while True:
            self.wake.wait()
            self.wake.clear()
            while self.repeating is not None:
                time.sleep(REPEAT_INTERVAL)     # after the real keydown, which Dota already has
                if (name := self.repeating) is not None:
                    controller.tap(name)
