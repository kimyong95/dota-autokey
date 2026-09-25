"""
Minimal Source 2 VConsole client (stdlib only) that runs server Lua and reads its reply.
Protocol credit: https://github.com/oxijoined/vconsole-python and Demon673/dota2-mcp.

Dota 2 opens the remote console on TCP 127.0.0.1:29000 in -tools mode only.

    with VConsoleClient() as vc:
        vc.run_lua("GetGroundHeight(Vector(0, 0, 0), nil)")  # "128"

    python vconsole_client.py getpos                 # run one console command, print its output

Wire format: 12-byte big-endian header (4s type, u16 version, u32 length incl. header,
u16 handle) then body. We send CMND (body = command + NUL) and read PRNT (text is a
NUL-terminated string at body offset 28); every other packet type is skipped.

The console is a broadcast log stream, so the Lua prints `REQUEST=<uid> VALUE=<value>` and
we wait for the line with our uid. Any failure (Lua error, lost connection) is just a timeout.
"""

import queue
import re
import socket
import struct
import threading
import time
import uuid

HEADER = struct.Struct(">4sHIH")
VERSION = 0xD4
MAX_COMMAND = 510  # longest console command Dota executes (measured); longer ones vanish


class VConsoleClient:
    def __init__(self, host: str = "127.0.0.1", port: int = 29000) -> None:
        self._sock = socket.create_connection((host, port), timeout=5.0)
        self._sock.settimeout(None)
        self._lines: queue.SimpleQueue[str] = queue.SimpleQueue()
        threading.Thread(target=self._listen, daemon=True).start()

    def __enter__(self) -> "VConsoleClient":
        return self

    def __exit__(self, *exc) -> None:
        self._sock.close()

    def run_cmd(self, cmd: str, timeout: float = 2.0) -> str | None:
        """Run a console command, e.g. "getpos", and return what it printed.

        Sent as one line, `echo REQUEST=<uid> BEGIN; <cmd>; echo REQUEST=<uid> END`. Dota runs
        the `;`-separated commands in order, so the lines between the two echoes are exactly
        its output (joined with newlines; "" if it printed nothing). Only output printed while
        the command runs is captured. Returns None on timeout. Dota silently drops commands
        over 510 chars (the echoes take 104 of them); longer ones raise ValueError.
        """
        uid = uuid.uuid4().hex
        begin, end = f"REQUEST={uid} BEGIN", f"REQUEST={uid} END"
        self._send(f"echo {begin}; {cmd}; echo {end}")
        deadline = time.monotonic() + timeout
        seen: list[str] = []
        try:
            while (line := self._lines.get(timeout=max(0.0, deadline - time.monotonic()))) != end:
                seen.append(line)
        except queue.Empty:
            return None
        return "\n".join(seen[seen.index(begin) + 1:])

    def run_lua(self, expr: str, timeout: float = 2.0) -> str | None:
        """Evaluate the Lua expression `expr` in the server VM and return it as a string.

        Returns None on timeout (including Lua errors). `expr` must be a single line, with
        single quotes only. Dota silently drops console commands over 510 chars, which leaves
        365 for `expr`; longer ones raise ValueError. A console line is cut
        at ~16383 chars, so a longer value comes back silently truncated.
        """
        uid = uuid.uuid4().hex
        lua = f"local value = {expr} print(string.format('REQUEST={uid} VALUE=%s', tostring(value)))"
        self._send(f'ent_fire dota_gamerules RunScriptCode "{lua}"')
        pattern = re.compile(rf"REQUEST={uid} VALUE=(.*)")
        deadline = time.monotonic() + timeout
        while (left := deadline - time.monotonic()) > 0:
            try:
                line = self._lines.get(timeout=left)
            except queue.Empty:
                break
            if m := pattern.fullmatch(line):
                return m.group(1)
        return None

    def _send(self, cmd: str) -> None:
        if len(cmd.encode()) > MAX_COMMAND:
            raise ValueError(f"console command is {len(cmd.encode())} chars, max {MAX_COMMAND}")
        body = cmd.encode() + b"\x00"
        self._sock.sendall(HEADER.pack(b"CMND", VERSION, HEADER.size + len(body), 0) + body)

    def _recv(self, n: int) -> bytes:
        buf = bytearray()
        while len(buf) < n:
            chunk = self._sock.recv(n - len(buf))
            if not chunk:
                raise ConnectionError("peer closed connection")
            buf += chunk
        return bytes(buf)

    def _listen(self) -> None:
        try:
            while True:
                kind, _, length, _ = HEADER.unpack(self._recv(HEADER.size))
                body = self._recv(length - HEADER.size)
                if kind == b"PRNT":
                    self._lines.put(body[28:].split(b"\x00", 1)[0].decode(errors="replace").rstrip("\r\n"))
        except OSError:
            pass


if __name__ == "__main__":
    # python vconsole_client.py getpos  -> runs the command and prints its output
    import sys

    with VConsoleClient() as vc:
        out = vc.run_cmd(" ".join(sys.argv[1:]))
    print(out)
