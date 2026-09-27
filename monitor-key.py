"""Print every key event: time, milliseconds since the same key's previous event, down / up, name, scan code.

Run: uv run python monitor-key.py      (Ctrl+C to stop)

Start it after invoker.py: the newest keyboard hook sees an event first, and invoker.py suppresses its keys
(F13-F15 and the spell keys), so a monitor started before it never sees them.
"""
import time
from datetime import datetime

import keyboard

last_event_time = {}    # scan code -> time of that key's previous event


def show(event):
    previous = last_event_time.get(event.scan_code)
    last_event_time[event.scan_code] = event.time
    since = f"{(event.time - previous) * 1000:9.1f} ms" if previous else " " * 12
    print(f"{datetime.fromtimestamp(event.time):%H:%M:%S.%f}"[:-3],
          since, f"{event.event_type:4}", event.name, f"(scan code {event.scan_code})")


keyboard.hook(show)
try:
    while True:
        time.sleep(1)
except KeyboardInterrupt:
    pass
