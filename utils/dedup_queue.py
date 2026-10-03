import threading


class DedupeQueue:
    """FIFO queue that drops items already waiting in it."""

    def __init__(self):
        self.items = []
        self.cond = threading.Condition()

    def put(self, item):
        with self.cond:
            if item not in self.items:
                self.items.append(item)
                self.cond.notify()

    def get(self, timeout=None):
        """The oldest item; None if none comes within `timeout` seconds (None: wait for ever)."""
        with self.cond:
            if not self.cond.wait_for(lambda: self.items, timeout):
                return None
            return self.items.pop(0)
