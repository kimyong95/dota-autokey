"""Dota's memory, in three classes:

    Offsets       where Dota's variables are: worked out offline from client.dll, saved to data/memory-offsets.json
    Process       a running process found by its window title, its memory read as raw bytes: the Windows calls
    MemoryReader  what Dota's variables hold, with their types, and what they add up to

MemoryReader uses Offsets and Process, which know nothing of each other. The application uses only MemoryReader:

    memory = MemoryReader()         # hooks the running Dota; ProcessLookupError if it is not running
    memory.get_view_matrix()        # 4 x 4 array (clip = matrix @ (x, y, z, 1)), or None before the first frame
    memory.get_hero_position()      # (x, y, z, yaw), or None while there is no hero: main menu, hero pick
    memory.get_hover_enemy_hero_position()  # (x, y, z) of the enemy hero under the cursor, or None while there is none

A MemoryReader lives inside one Dota session: once Dota exits, its reads raise ProcessLookupError.

The view-matrix and entity-system patterns are from dota2-dumper by Anroshka
(https://github.com/Anroshka/dota2-dumper), a Dota port of cs2-dumper by a2x (https://github.com/a2x/cs2-dumper).
"""
import json
import struct
from pathlib import Path

import numpy as np
import pywintypes
import win32api
import win32con
import win32gui
import win32process

from utils.gsi import find_dota


class Offsets:
    """Where Dota's variables are, by friendly name: computed offline from client.dll (Dota need not run) and saved
    to PATH, tagged with Dota's build and client.dll's link timestamp.

        offsets = Offsets.load()        # the saved ones; updated first if missing, from another client.dll,
                                        # or not for the definitions below
        offsets = Offsets.update()      # recompute and save
        offsets["node-position"]        # a value by friendly name
        offsets.build                   # Dota's build number
        offsets.matches(header)         # whether they are for the client.dll with this PE header

    Friendly names are "object-variable": the object that holds the variable (none for client.dll's globals),
    then what it is. Three kinds:
        PATTERN   global variables, found through the code that uses them: module offsets in client.dll
        SCHEMA    class sizes and field offsets, looked up by schema name in Valve's schema tables
        CONSTANT  fixed parts of Source 2's entity list, which are in neither
    """

    PATH = Path(__file__).parents[1] / "data" / "memory-offsets.json"

    # Each global variable as code that refers to it: byte values, None where any byte may be. The first four None
    # are the variable's address, relative to the end of those four bytes.
    PATTERN = {
        # lea rcx, [view matrices]; shl rax, 6 (64-byte matrices)
        "view-matrix": (0x48, 0x8D, 0x0D, None, None, None, None, 0x48, 0xC1, 0xE0, 0x06),
        # mov rbx, [entity system]; mov [a copy], rbx; movsxd r14, [rbx + ...]
        "entity-system": (0x48, 0x8B, 0x1D, None, None, None, None,
                          0x48, 0x89, 0x1D, None, None, None, None, 0x4C, 0x63, 0xB3),
        # mov rdx, [local player controller]; test rdx, rdx; jz ...; mov edx, [rdx + m_hPawn]
        "local-controller": (0x48, 0x8B, 0x15, None, None, None, None,
                             0x48, 0x85, 0xD2, 0x74, None, 0x8B, 0x92, 0xA4, 0x06, 0x00, 0x00),
        # mov edi, [hovered entity]; xor edx, edx; mov rcx, r13; call ... (a field of Dota's input object, CDOTAInput)
        "hovered-entity": (0x8B, 0x3D, None, None, None, None, 0x33, 0xD2, 0x49, 0x8B, 0xCD, 0xE8),
    }

    # Friendly name -> schema name: "Class" for the class's size, "Class.field" for the offset of a field in the
    # class that declares it.
    SCHEMA = {
        "entry-size": "CEntityIdentity",                                # an entry of the entity list
        "entry-designer-name": "CEntityIdentity.m_designerName",        # e.g. "npc_dota_hero_tinker"
        "controller-hero": "C_DOTAPlayerController.m_hAssignedHero",    # your hero, as a handle
        "entity-scene-node": "C_BaseEntity.m_pGameSceneNode",           # where an entity is
        "entity-team": "C_BaseEntity.m_iTeamNum",                       # 2 Radiant, 3 Dire, 4 neutral
        "node-position": "CGameSceneNode.m_vecAbsOrigin",               # world x, y, z
        "node-rotation": "CGameSceneNode.m_angAbsRotation",             # world pitch, yaw, roll, in degrees
    }

    CONSTANT = {
        "entity-chunks": 0x10,      # in the entity system: pointers to the chunks of entity list entries
        "chunk-size": 512,          # entries per chunk
        "entry-entity": 0x0,        # in an entry: pointer to the entity
        "handle-index": 0x7FFF,     # the entity index bits of a handle; the bits above are a serial number
    }

    def __init__(self, saved):
        self.build, self.timestamp = saved["build"], saved["client-timestamp"]
        self.values = {**saved["pattern"], **saved["constant"], **{name: saved["schema"][schema] for name, schema in self.SCHEMA.items()}}

    def __getitem__(self, name):
        """A pattern, schema or constant value by friendly name."""
        return self.values[name]

    def matches(self, header):
        """Whether these offsets are for the client.dll whose first bytes (from its file, or as loaded) are header."""
        return self.link_timestamp(header) == self.timestamp

    @classmethod
    def load(cls, dll=None):
        """Offsets for the client.dll file at `dll` (default: the installed one)."""
        dll = dll or cls.installed_dll()
        saved = json.loads(cls.PATH.read_text()) if cls.PATH.exists() else {}
        with dll.open("rb") as file:
            timestamp = cls.link_timestamp(file.read(0x1000))
        current = (saved.get("client-timestamp") == timestamp
                   and saved.get("pattern", {}).keys() == cls.PATTERN.keys()
                   and saved.get("schema", {}).keys() == set(cls.SCHEMA.values())
                   and saved.get("constant") == cls.CONSTANT)
        return cls(saved) if current else cls.update(dll)

    @classmethod
    def update(cls, dll=None):
        """Compute every offset from the client.dll file at `dll` (default: the installed one) and save them."""
        dll = dll or cls.installed_dll()
        image, base, code = cls.load_image(dll)
        steam_inf = (dll.parents[2] / "steam.inf").read_text()        # game/dota/steam.inf
        saved = {
            "build": int(dict(line.split("=", 1) for line in steam_inf.splitlines() if "=" in line)["ClientVersion"]),
            "client-timestamp": cls.link_timestamp(image),
            "pattern": {name: cls.find_pattern(name, image, code, pattern) for name, pattern in cls.PATTERN.items()},
            "schema": {schema: cls.find_schema(schema, image, base) for schema in cls.SCHEMA.values()},
            "constant": cls.CONSTANT,
        }
        cls.PATH.write_text(json.dumps(saved, indent=4) + "\n")
        return cls(saved)

    @staticmethod
    def installed_dll():
        if not (dota := find_dota()):
            raise RuntimeError("no Dota install found")
        return dota / "game/dota/bin/win64/client.dll"

    @staticmethod
    def link_timestamp(header):
        """The link timestamp in a DLL's PE header (its first bytes): it differs per build."""
        return struct.unpack_from("<I", header, struct.unpack_from("<I", header, 0x3C)[0] + 8)[0]

    @staticmethod
    def load_image(dll):
        """The DLL file laid out as when loaded, so image[module offset] is the byte there.

        Returns (image, the image base its absolute pointers assume, [(start, end)] of its code).
        """
        data = dll.read_bytes()
        pe = struct.unpack_from("<I", data, 0x3C)[0]
        image = bytearray(struct.unpack_from("<I", data, pe + 0x50)[0])         # SizeOfImage
        headers = struct.unpack_from("<I", data, pe + 0x54)[0]                   # SizeOfHeaders
        image[:headers] = data[:headers]
        code = []
        table = pe + 24 + struct.unpack_from("<H", data, pe + 20)[0]           # past the optional header
        for header in range(table, table + 40 * struct.unpack_from("<H", data, pe + 6)[0], 40):
            size, start, file_size, file_start = struct.unpack_from("<IIII", data, header + 8)
            length = min(size, file_size)
            image[start:start + length] = data[file_start:file_start + length]
            if struct.unpack_from("<I", data, header + 36)[0] & 0x20000000:   # IMAGE_SCN_MEM_EXECUTE
                code.append((start, start + size))
        return image, struct.unpack_from("<Q", data, pe + 0x30)[0], code

    @staticmethod
    def find_all(data, needle, start=0, end=None):
        """Every position of needle in data[start:end]."""
        at = data.find(needle, start, end)
        while at >= 0:
            yield at
            at = data.find(needle, at + 1, end)

    @classmethod
    def find_pattern(cls, name, image, code, pattern):
        """Module offset of the global variable that the code matching pattern refers to."""
        address = pattern.index(None)       # the relative address; the known bytes before it anchor the search
        anchor = bytes(pattern[:address])
        targets = {at + address + 4 + struct.unpack_from("<i", image, at + address)[0]
                   for start, end in code for at in cls.find_all(image, anchor, start, end)
                   if all(want in (None, got) for want, got in zip(pattern, image[at:at + len(pattern)]))}
        if len(targets) != 1:
            raise RuntimeError(f"{name}: pattern points at {len(targets)} places, not 1; Dota changed that code")
        return targets.pop()

    # The schema tables, as compiled into the DLL: a class entry has its name at +0x08, its size (int32) at +0x20,
    # its field count (int16) at +0x24 and its fields at +0x30; a field entry is 0x20 bytes, with its name at
    # +0x00 and its offset (int32) at +0x10. Pointers are absolute, for the image base; links made at load are blank.

    @classmethod
    def find_schema(cls, name, image, base):
        """Size of a class ("Class"), or offset of a field ("Class.field"), from the schema tables."""
        def text(at):
            """The text that the pointer at `at` points at; "" if it points outside the image."""
            start = struct.unpack_from("<Q", image, at)[0] - base if 0 <= at < len(image) - 8 else -1
            return image[start:image.find(b"\0", start)].decode(errors="replace") if 0 <= start < len(image) else ""

        class_name, _, field = name.partition(".")
        for name_text in cls.find_all(image, b"\0" + class_name.encode() + b"\0"):
            for at in cls.find_all(image, struct.pack("<Q", base + name_text + 1)):
                entry = at - 8                                  # a pointer to the class name sits at +0x08
                fields = struct.unpack_from("<Q", image, entry + 0x30)[0] - base
                count = struct.unpack_from("<h", image, entry + 0x24)[0]
                names = [text(fields + 0x20 * i) for i in range(count)] if 0 < count < 4096 else []
                if names and names[0].startswith("m_"):         # a class entry, not some other use of the name
                    if not field:
                        return struct.unpack_from("<i", image, entry + 0x20)[0]
                    if field in names:
                        return struct.unpack_from("<i", image, fields + 0x20 * names.index(field) + 0x10)[0]
        raise RuntimeError(f"{name}: not in client.dll's schema tables; Valve renamed or removed it")


class Process:
    """A running process found by its window title, hooked on creation, and its memory read as raw bytes: only the
    Windows calls, nothing about what the process is.

        process = Process("Dota 2")                 # ProcessLookupError if there is no such window
        base, path = process.module("client.dll")   # where a DLL is loaded, and its file
        process.read(address, size)                 # bytes
        process.unpack("<3f", address)              # values, decoded with a struct format
        process.text(address)                       # NUL-terminated text

    Reads raise ProcessLookupError once the process has exited, and pywintypes.error at an unreadable address.
    """

    def __init__(self, window_title):
        self.name = window_title
        window = win32gui.FindWindow(None, window_title)
        if not window:
            raise ProcessLookupError(f'no "{window_title}" window: start it first')
        pid = win32process.GetWindowThreadProcessId(window)[1]
        self.handle = win32api.OpenProcess(win32con.PROCESS_QUERY_INFORMATION | win32con.PROCESS_VM_READ, False, pid)

    def module(self, name):
        """(base address, file path) of a loaded module."""
        for base in win32process.EnumProcessModulesEx(self.handle, win32process.LIST_MODULES_ALL):
            path = Path(win32process.GetModuleFileNameEx(self.handle, base))
            if path.name.lower() == name:
                return base, path
        raise ProcessLookupError(f'"{self.name}" has not loaded {name}')

    def read(self, address, size):
        """size bytes at an address."""
        try:
            return win32process.ReadProcessMemory(self.handle, address, size)
        except pywintypes.error:    # the same error for an unreadable address and an exited process
            if win32process.GetExitCodeProcess(self.handle) != win32con.STILL_ACTIVE:
                raise ProcessLookupError(f'"{self.name}" has exited') from None
            raise

    def unpack(self, fmt, address):
        """The values at an address, decoded with a struct format."""
        return struct.unpack(fmt, self.read(address, struct.calcsize(fmt)))

    def text(self, address, limit=64):
        """The NUL-terminated text at an address."""
        return self.read(address, limit).split(b"\0")[0].decode(errors="replace")


class MemoryReader:
    """Dota's memory as the application needs it: the running Dota, read with Offsets for the client.dll it loaded,
    each variable decoded as its TYPE.

        memory = MemoryReader()                     # ProcessLookupError if Dota is not running
        memory.get_view_matrix()                    # 4 x 4 array, or None before Dota draws a frame
        memory.get_hero_position()                  # (x, y, z, yaw), or None while there is no hero
        memory.get_hover_enemy_hero_position()      # (x, y, z) of the enemy hero under the cursor, or None
        memory.read("controller-hero", controller)  # one variable of the object at an address

    Meant to live inside one Dota session: every read raises ProcessLookupError once Dota has exited. Holds nothing
    that changes after creation, so threads can share one.
    """

    POINTER, HANDLE, TEAM, VECTOR, ANGLES, MATRIX = "<Q", "<I", "<B", "<3f", "<3f", "<16f"    # what Dota's memory holds
    NO_ENTITY = 0xFFFFFFFF          # a handle to no entity

    TYPE = {
        "view-matrix": MATRIX,              # world to clip space, row-major
        "entity-system": POINTER,
        "local-controller": POINTER,        # your player controller
        "hovered-entity": HANDLE,           # the entity under the cursor; NO_ENTITY when there is none
        "entity-chunks": POINTER,           # one per chunk, to its entries
        "entry-entity": POINTER,
        "entry-designer-name": POINTER,     # to text
        "controller-hero": HANDLE,
        "entity-scene-node": POINTER,
        "entity-team": TEAM,
        "node-position": VECTOR,            # world x, y, z
        "node-rotation": ANGLES,            # pitch, yaw, roll in degrees
    }

    def __init__(self):
        self.process = Process("Dota 2")
        self.client, dll = self.process.module("client.dll")
        self.offsets = Offsets.load(dll)            # recomputed from that file first if outdated
        if not self.offsets.matches(self.process.read(self.client, 0x1000)):
            raise RuntimeError("client.dll changed on disk while Dota was running: restart Dota")

    def read(self, name, base=None):
        """Variable `name` of the object at `base` (default: client.dll, for global variables), as its TYPE."""
        value = self.process.unpack(self.TYPE[name], (self.client if base is None else base) + self.offsets[name])
        return value[0] if len(value) == 1 else value

    def entry(self, handle):
        """Address of the entity list entry that a handle refers to; 0 if its chunk does not exist."""
        index, chunk_size = handle & self.offsets["handle-index"], self.offsets["chunk-size"]
        chunk = self.read("entity-chunks", self.read("entity-system") + 8 * (index // chunk_size))    # 8-byte pointers
        return chunk and chunk + self.offsets["entry-size"] * (index % chunk_size)

    def get_view_matrix(self):
        """Dota's view matrix of the last frame drawn, world to clip space: clip = matrix @ (x, y, z, 1).

        A 4 x 4 array, or None before Dota has drawn a frame. One read.
        """
        matrix = np.array(self.read("view-matrix")).reshape(4, 4)
        return matrix if matrix.any() else None

    def get_hero_position(self):
        """(x, y, z, yaw) of your hero, or None while there is none: main menu, hero pick. 10 reads.

        x, y, z is the world position; yaw the facing in degrees, 0..360: 0 = +x (east), 90 = +y (north), as in GSI.
        """
        controller = self.read("local-controller")
        entry = controller and self.entry(self.read("controller-hero", controller))
        hero = entry and self.read("entry-entity", entry)
        name = hero and self.read("entry-designer-name", entry)
        if not name or not self.process.text(name).startswith("npc_dota_hero_"):
            return None
        node = self.read("entity-scene-node", hero)
        pos = self.read("node-position", node)
        yaw = self.read("node-rotation", node)[1]       # of pitch, yaw, roll
        return *pos, yaw % 360

    def get_hover_enemy_hero_position(self):
        """(x, y, z) of the enemy hero under the cursor, as Dota picks it, or None while there is none: nothing, not a
        hero, or on your team. 11 reads; 1 for nothing under the cursor.

        The hero's own world position, where it stands: not the point under the cursor. A hero is an npc_dota_hero_
        entity (the Hero Demo's target dummy is one), an enemy one on any team but your player controller's.
        Illusions count: the game does not tell an enemy's apart either.
        """
        handle = self.read("hovered-entity")
        entry = handle != self.NO_ENTITY and self.entry(handle)
        entity = entry and self.read("entry-entity", entry)
        name = entity and self.read("entry-designer-name", entry)
        if not name or not self.process.text(name).startswith("npc_dota_hero_"):
            return None
        controller = self.read("local-controller")
        if not controller or self.read("entity-team", entity) == self.read("entity-team", controller):
            return None
        return self.read("node-position", self.read("entity-scene-node", entity))
