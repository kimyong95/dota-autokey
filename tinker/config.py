import threading
from pathlib import Path

import yaml


class TinkerConfig:
    """The Tinker scripts' settings: lists of strings by section in a YAML file, so they outlast a restart.

        config = TinkerConfig()             # config.yaml, next to this file
        config.read("combo")                # ["d", "e", "f", "q"]; [] if the file or the section is missing
        config.toggle("combo", "w")         # adds "w", or removes it if there; returns the new list

    Sections: combo and extra_keys (run.py), repeat_keys (TinkerRepeatKey). Share one per file between threads:
    a toggle rereads the file under its lock first, so the other sections stay as their writers left them.
    """

    def __init__(self, path=Path(__file__).with_name("config.yaml")):
        self.path = path
        self.lock = threading.Lock()

    def read(self, section):
        """The list `section`, as strings; [] if the file or the section is missing."""
        return [str(item) for item in self.load().get(section) or []]

    def toggle(self, section, item):
        """Add `item` to the list `section`, or remove it if there; returns the new list."""
        with self.lock:
            config = self.load()
            items = [str(i) for i in config.get(section) or []]
            if item in items:
                items.remove(item)
            else:
                items.append(item)
            config[section] = items
            self.path.write_text(yaml.safe_dump(config, sort_keys=False))
        return items

    def load(self):
        """The whole file; {} if it is missing or empty."""
        return (yaml.safe_load(self.path.read_text()) if self.path.exists() else None) or {}
