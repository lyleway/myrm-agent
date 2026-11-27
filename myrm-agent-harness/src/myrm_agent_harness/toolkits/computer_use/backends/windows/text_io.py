"""Windows desktop text input — Unicode keyboard events through SendInput.

[INPUT]
- (none: Win32 through ctypes only)

[OUTPUT]
- type_unicode_text: types any text through SendInput Unicode key events, no clipboard involved

[POS]
Text bridge between the Windows desktop and the Windows backend: layout-independent typing for
non-ASCII input. Win32 is resolved at call time so the module imports on any platform.
"""

from __future__ import annotations

import ctypes
import functools
import time
from ctypes import wintypes

_INPUT_KEYBOARD = 1
_KEYEVENTF_KEYUP = 0x0002
_KEYEVENTF_UNICODE = 0x0004
_VK_RETURN = 0x0D


# INPUT is a union of MOUSEINPUT / KEYBDINPUT / HARDWAREINPUT; SendInput rejects any cbSize other than the
# real sizeof(INPUT), so every member is declared with fixed-width fields (the ``wintypes`` aliases change
# size per platform) and the union keeps all three members to take the size of the largest.
class _MouseInput(ctypes.Structure):
    _fields_ = (
        ("dx", ctypes.c_int32),
        ("dy", ctypes.c_int32),
        ("mouseData", ctypes.c_uint32),
        ("dwFlags", ctypes.c_uint32),
        ("time", ctypes.c_uint32),
        ("dwExtraInfo", ctypes.c_size_t),
    )


class _KeybdInput(ctypes.Structure):
    _fields_ = (
        ("wVk", ctypes.c_uint16),
        ("wScan", ctypes.c_uint16),
        ("dwFlags", ctypes.c_uint32),
        ("time", ctypes.c_uint32),
        ("dwExtraInfo", ctypes.c_size_t),
    )


class _HardwareInput(ctypes.Structure):
    _fields_ = (
        ("uMsg", ctypes.c_uint32),
        ("wParamL", ctypes.c_uint16),
        ("wParamH", ctypes.c_uint16),
    )


class _InputUnion(ctypes.Union):
    _fields_ = (("mi", _MouseInput), ("ki", _KeybdInput), ("hi", _HardwareInput))


class _Input(ctypes.Structure):
    _anonymous_ = ("u",)
    _fields_ = (("type", ctypes.c_uint32), ("u", _InputUnion))


@functools.cache
def _user32() -> ctypes.CDLL:
    """Return user32 with the ``SendInput`` prototype declared.

    A private library instance keeps the prototype off the process-wide ``ctypes.windll`` object other
    libraries share.
    """
    user32: ctypes.CDLL = ctypes.WinDLL("user32", use_last_error=True)  # type: ignore[attr-defined]
    user32.SendInput.argtypes = (wintypes.UINT, ctypes.POINTER(_Input), ctypes.c_int)
    user32.SendInput.restype = wintypes.UINT
    return user32


def _key_events(text: str) -> list[tuple[int, int, int]]:
    """Expand *text* into ``(wVk, wScan, flags)`` key presses.

    Characters become Unicode key events carrying one UTF-16 code unit each, so a character outside the
    BMP is sent as its surrogate pair. Line breaks become a real Return key because editors and form
    fields act on that key, not on a Unicode packet holding ``\\n``.
    """
    presses: list[tuple[int, int, int]] = []
    for char in text:
        if char == "\n":
            presses.append((_VK_RETURN, 0, 0))
            continue
        units = char.encode("utf-16-le")
        presses.extend(
            (0, int.from_bytes(units[i : i + 2], "little"), _KEYEVENTF_UNICODE) for i in range(0, len(units), 2)
        )
    return presses


def _send_key_presses(presses: list[tuple[int, int, int]]) -> None:
    """Insert each press as a key-down / key-up pair; raise when Windows takes fewer events than sent."""
    events = (_Input * (2 * len(presses)))()
    for index, (vk, scan, flags) in enumerate(presses):
        for offset, event_flags in enumerate((flags, flags | _KEYEVENTF_KEYUP)):
            event = events[2 * index + offset]
            event.type = _INPUT_KEYBOARD
            event.ki.wVk = vk
            event.ki.wScan = scan
            event.ki.dwFlags = event_flags
    sent = _user32().SendInput(len(events), events, ctypes.sizeof(_Input))
    if sent != len(events):
        last_error = ctypes.get_last_error()  # type: ignore[attr-defined]
        raise OSError(f"SendInput inserted {sent} of {len(events)} key events (last error {last_error})")


def type_unicode_text(text: str, delay_ms: int, chunk_size: int) -> None:
    """Type *text* into the focused window through Unicode key events.

    Unlike a clipboard paste this leaves the user's clipboard untouched and works regardless of keyboard
    layout. Windows blocks input aimed at a window of higher integrity (UIPI) without reporting it, so
    a ``SendInput`` that succeeds does not prove the text arrived.

    Args:
        text: Text to type; ``\\r\\n`` and ``\\r`` are typed as one Return.
        delay_ms: Pause between chunks, so the target's message loop is never flooded.
        chunk_size: Characters sent per ``SendInput`` call.

    Raises:
        OSError: Windows inserted fewer key events than requested.
    """
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    for start in range(0, len(normalized), chunk_size):
        if start:
            time.sleep(delay_ms / 1000.0)
        _send_key_presses(_key_events(normalized[start : start + chunk_size]))
