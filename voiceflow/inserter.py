"""Insert text at the cursor in whatever app is focused.

Fastest reliable method on macOS: put the text on the pasteboard and synthesize
Cmd+V via a Quartz key event. Optionally restores the previous clipboard a
moment later so we don't clobber what the user had copied.
"""

from __future__ import annotations

import logging
import threading
import time

import Quartz
from AppKit import NSPasteboard

log = logging.getLogger("voiceflow.inserter")

_V_KEYCODE = 9  # virtual keycode for the "v" key
_UTF8 = "public.utf8-plain-text"


def _accessibility_trusted():
    try:
        from ApplicationServices import AXIsProcessTrusted

        return bool(AXIsProcessTrusted())
    except Exception:
        return None


def _paste_keystroke() -> None:
    src = Quartz.CGEventSourceCreate(Quartz.kCGEventSourceStateHIDSystemState)
    down = Quartz.CGEventCreateKeyboardEvent(src, _V_KEYCODE, True)
    up = Quartz.CGEventCreateKeyboardEvent(src, _V_KEYCODE, False)
    Quartz.CGEventSetFlags(down, Quartz.kCGEventFlagMaskCommand)
    Quartz.CGEventSetFlags(up, Quartz.kCGEventFlagMaskCommand)
    Quartz.CGEventPost(Quartz.kCGHIDEventTap, down)
    Quartz.CGEventPost(Quartz.kCGHIDEventTap, up)


def insert_text(text: str, restore_clipboard: bool = True) -> None:
    if not text:
        log.info("insert: empty text, nothing to paste")
        return
    trusted = _accessibility_trusted()
    log.info("insert: %d chars, accessibility_trusted=%s", len(text), trusted)
    if trusted is False:
        log.warning("insert: NOT accessibility-trusted — Cmd+V will be dropped by macOS")

    pb = NSPasteboard.generalPasteboard()

    previous = None
    if restore_clipboard:
        previous = pb.stringForType_(_UTF8)

    pb.clearContents()
    ok = pb.setString_forType_(text, _UTF8)
    log.info("insert: clipboard set ok=%s; posting Cmd+V", ok)

    # Give the pasteboard a beat to settle before the paste keystroke.
    time.sleep(0.03)
    _paste_keystroke()

    if restore_clipboard and previous is not None:
        def _restore():
            time.sleep(0.25)  # wait until the target app has consumed the paste
            pb.clearContents()
            pb.setString_forType_(previous, _UTF8)

        threading.Thread(target=_restore, daemon=True).start()
