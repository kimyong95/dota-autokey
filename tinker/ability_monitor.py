"""Tinker's ability states read off the screen, not GSI: available, casting, cooldown or no mana.

    monitor = TinkerAbilityMonitor()
    monitor.activate()                      # watch the icons for the next ACTIVE_SECONDS; call again to keep watching
    state = monitor.states["tinker_laser"]  # its AbilityState, updated every frame while active
    state.castable                          # available, or not known (None)
    state.on_casting = f                    # f() when any other state, None too, turns casting: the cast point began
    state.on_casted = f                     # f() when casting turns into cooldown: the cast went off
    state.on_available = f                  # f() when any other state, None too, turns available

While active, its own thread grabs the six ability icons every frame (a GDI copy waits for the next composed
frame: ~16.7 ms at 60 Hz). ACTIVE_SECONDS after the last activate() it stops and resets every state to None, so
every ability counts as castable until it watches again. Ctrl + CAPTURE_KEY, in Dota with all six abilities
castable, saves each ability icon on screen as its reference, assets/<ability>.png.

Each icon and its reference are shrunk to 32x32 and compared:
  casting      its share of green pixels (G - B above 25) is over 3 points above the reference's. Dota sweeps a
               green wash over the icon during the cast point; cooldown greys it and no mana turns it blue, which
               only lower that share.
  available    otherwise, if the RMS difference is below 35.
  no_mana      otherwise, if over 40 % of its pixels are blue (B - R above 20): no mana washes the icon blue. A
               cooldown without the mana for the next cast looks the same, with its countdown on top.
  cooldown     otherwise: cooldown greys and darkens the icon.
On 4K, 1080p and 900p screenshots: available RMS ~1 at the references' resolution and <= 22 at another, the
others >= 45; casting raises the green share >= 13 points, the other states <= 0.4. On one 4K screenshot each,
the blue share is <= 0.19 on cooldown and >= 0.63 without mana (0.63: Warp Flare on cooldown without mana).

Assumes the borderless window's HUD with six abilities (Tinker with Aghanim's Shard).
"""
import ctypes
import threading
import time
from contextlib import contextmanager
from pathlib import Path

import keyboard
import numpy as np
import win32con
import win32gui
import win32ui
from PIL import Image

from utils.window import LogicalScreen, dota_is_foreground, hud_surface

CAPTURE_KEY = "f6"                  # Dota's screenshot key (bind "F6" "jpeg"); held with Ctrl
ACTIVE_SECONDS = 0.5
ABILITIES = ["tinker_laser", "tinker_march_of_the_machines", "tinker_deploy_turrets",     # HUD slot order:
             "tinker_warp_grenade", "tinker_keen_teleport", "tinker_rearm"]              # Q W E D SPACE F
ICON_LEFT, ICON_TOP, ICON_SIZE, ICON_STEP = 1561, 1894, 96, 116     # logical (4K) pixels, off Dota's HUD
ASSETS = Path(__file__).parents[1] / "assets"


@contextmanager
def screen_grabber(left, top, width, height):
    """Yields grab(): that screen pixel rect as an RGB array. Not mss: its CAPTUREBLT makes the cursor flicker."""
    screen_dc = win32gui.GetDC(0)
    source = win32ui.CreateDCFromHandle(screen_dc)
    memory = source.CreateCompatibleDC()
    bitmap = win32ui.CreateBitmap()
    bitmap.CreateCompatibleBitmap(source, width, height)
    memory.SelectObject(bitmap)

    def grab():
        memory.BitBlt((0, 0), (width, height), source, (left, top), win32con.SRCCOPY)
        return np.frombuffer(bitmap.GetBitmapBits(True), np.uint8).reshape(height, width, 4)[..., 2::-1]
    try:
        yield grab
    finally:
        win32gui.DeleteObject(bitmap.GetHandle())
        memory.DeleteDC()
        source.DeleteDC()
        win32gui.ReleaseDC(0, screen_dc)


def ability_row():
    """Screen pixel rect around the ability icons, and each icon's (left, width) within it."""
    screen = LogicalScreen(*hud_surface())
    rects = [screen.rect(ICON_LEFT + slot * ICON_STEP, ICON_TOP, ICON_SIZE, ICON_SIZE) for slot in range(len(ABILITIES))]
    left, top, _, height = rects[0]
    return (left, top, rects[-1][0] + rects[-1][2] - left, height), [(x - left, width) for x, _, width, _ in rects]


def icons(row_image, spans):
    return [Image.fromarray(row_image[:, x:x + width]) for x, width in spans]


def small(icon):
    return np.asarray(icon.convert("RGB").resize((32, 32), Image.BOX), np.float32)


def classify(icon, ref):
    green_share = lambda image: (image[..., 1] - image[..., 2] > 25).mean()
    green_rise = green_share(icon) - green_share(ref)
    difference = np.sqrt(((icon - ref) ** 2).mean())
    blue_share = (icon[..., 2] - icon[..., 0] > 20).mean()
    if green_rise > 0.03:
        return "casting"
    elif difference < 35:
        return "available"
    elif blue_share > 0.4:
        return "no_mana"
    else:
        return "cooldown"


def load_references():
    paths = [ASSETS / f"{ability}.png" for ability in ABILITIES]
    return [small(Image.open(path)) for path in paths] if all(path.exists() for path in paths) else None


class AbilityState:
    """One ability's state off its icon: "available", "casting", "cooldown" or "no_mana"; None while not known.

    Set the callbacks to be told of a change; they run on the monitor's thread, so they should return quickly.
    """

    def __init__(self):
        self.state = None
        self.on_casting = None          # f(): any other state, None too, turned casting
        self.on_casted = None           # f(): casting turned into cooldown
        self.on_available = None        # f(): any other state, None too, turned available

    @property
    def castable(self):
        """Available, or not known (None): castable as far as anyone can tell."""
        return self.state in (None, "available")

    def update(self, state):
        """The state in the latest frame; calls a callback if it changed into one."""
        previous, self.state = self.state, state
        if previous != "casting" and state == "casting" and self.on_casting:
            self.on_casting()
        if previous == "casting" and state == "cooldown" and self.on_casted:
            self.on_casted()
        if previous != "available" and state == "available" and self.on_available:
            self.on_available()

    def reset(self):
        """Not known any more (None), as before the first look: the monitor stopped watching."""
        self.state = None


class TinkerAbilityMonitor:
    """Watches the ability icons while activated; call activate() and read `states` from any thread."""

    def __init__(self):
        ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))   # physical pixels; Qt sets it already
        ASSETS.mkdir(exist_ok=True)
        self.states = {ability: AbilityState() for ability in ABILITIES}
        self.active_until = 0.0
        self.woken = threading.Event()
        self.references = load_references()
        if self.references is None:
            print(f"No ability references yet: press Ctrl + {CAPTURE_KEY.upper()} in Dota while all abilities are castable.")
        keyboard.on_press_key(CAPTURE_KEY, self.capture)
        threading.Thread(target=self.run, daemon=True).start()

    def capture(self, event):
        """On Ctrl + CAPTURE_KEY, save each ability icon on screen now as its castable reference."""
        if not keyboard.is_pressed("ctrl"):     # a plain screenshot
            return
        if not dota_is_foreground():        # would save whatever covers the HUD
            return print(f"Ctrl + {CAPTURE_KEY.upper()} ignored: Dota is not the foreground window.")
        row, spans = ability_row()
        with screen_grabber(*row) as grab:
            row_image = grab()
        for ability, icon in zip(ABILITIES, icons(row_image, spans)):
            icon.save(ASSETS / f"{ability}.png")
        self.references = load_references()
        print(f"Saved ability references to {ASSETS}.")

    def activate(self):
        """Watch the icons until ACTIVE_SECONDS from now."""
        self.active_until = time.monotonic() + ACTIVE_SECONDS
        self.woken.set()

    def run(self):
        while True:
            self.woken.wait()
            self.woken.clear()
            if self.references is None:
                continue
            row, spans = ability_row()
            with screen_grabber(*row) as grab:
                while time.monotonic() < self.active_until:
                    for ability, icon, ref in zip(ABILITIES, icons(grab(), spans), self.references):
                        self.states[ability].update(classify(small(icon), ref))
            for state in self.states.values():
                state.reset()
