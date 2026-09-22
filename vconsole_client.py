"""
Minimal Source 2 VConsole client (stdlib only).
Protocol credit: https://github.com/oxijoined/vconsole-python and Demon673/dota2-mcp.

Dota 2 opens the remote console on TCP 127.0.0.1:29000 in -tools mode only.

    with VConsoleClient() as vc:
        lines = vc.run("status")               # console output of exactly that command
        lines = vc.run_script("check_ground")  # script_reload_code; raises on Lua errors

Wire format: 12-byte big-endian header (4s type, u16 version, u32 length incl. header,
u16 handle) then body. We send CMND (body = command + NUL) and read PRNT (text is a
NUL-terminated string at body offset 28); every other packet type is skipped.

`run()` brackets the command between `echo VC_BEGIN_<id>` and `echo VC_END_<id>`.
Dota executes console commands in order, so the lines between the markers are exactly
the command's output; the console history Dota replays on connect never leaks in, and
END is a positive "finished" signal. Only output printed synchronously by the command
is captured ("Unknown command: ..." is printed a frame later and is missed).
"""

import queue
import socket
import struct
import threading
import time
import uuid

HEADER = struct.Struct(">4sHIH")
VERSION = 0xD4


class VConsoleError(Exception):
    pass


class VConsoleClient:
    def __init__(self, host: str = "127.0.0.1", port: int = 29000, timeout: float = 5.0) -> None:
        try:
            self._sock = socket.create_connection((host, port), timeout=timeout)
        except OSError as e:
            raise VConsoleError(f"connect to {host}:{port} failed: {e}") from e
        self._sock.settimeout(None)
        self._lines: queue.SimpleQueue[str | None] = queue.SimpleQueue()  # None = listener died
        self._error: Exception | None = None
        threading.Thread(target=self._listen, daemon=True).start()

    def __enter__(self) -> "VConsoleClient":
        return self

    def __exit__(self, *exc) -> None:
        self._sock.close()

    def run(self, cmd: str, timeout: float = 5.0) -> list[str]:
        tag = uuid.uuid4().hex
        begin, end = f"VC_BEGIN_{tag}", f"VC_END_{tag}"
        for c in (f"echo {begin}", cmd, f"echo {end}"):
            self._send(c)
        deadline = time.monotonic() + timeout
        lines: list[str] = []
        started = False
        while True:
            line = self._next(deadline, cmd)
            if not started:
                started = begin in line
            elif end in line:
                return lines
            else:
                lines.append(line)

    def run_script(self, name: str, timeout: float = 5.0) -> list[str]:
        lines = self.run(f"script_reload_code {name}", timeout)
        for line in lines:
            if "Script Runtime Error" in line or "Script not found" in line:
                raise VConsoleError(f"{name}: {line}")
        return lines

    def _send(self, cmd: str) -> None:
        body = cmd.encode() + b"\x00"
        try:
            self._sock.sendall(HEADER.pack(b"CMND", VERSION, HEADER.size + len(body), 0) + body)
        except OSError as e:
            raise VConsoleError(f"send failed: {e}") from e

    def _next(self, deadline: float, cmd: str) -> str:
        try:
            line = self._lines.get(timeout=max(0.0, deadline - time.monotonic()))
        except queue.Empty:
            raise VConsoleError(f"timed out waiting for output of {cmd!r}") from None
        if line is None:
            raise VConsoleError(f"connection lost while running {cmd!r}") from self._error
        return line

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
                    self._lines.put(body[28:].split(b"\x00", 1)[0].decode(errors="replace").rstrip("\n"))
        except Exception as e:
            self._error = e
        finally:
            self._lines.put(None)
