from pynput.keyboard import Key


def key_name(key):
    """A pynput key's name: '0'-'9' or 'a'-'z' whatever modifiers are held, or a special key's ("f13", "space");
    None for other keys.

    Digits and letters from the virtual-key code, since the character changes with modifiers (Ctrl + Z is '\\x1a')."""
    if isinstance(key, Key):
        return key.name
    vk = getattr(key, "vk", None)
    return chr(vk).lower() if vk is not None and (0x30 <= vk <= 0x39 or 0x41 <= vk <= 0x5A) else None
