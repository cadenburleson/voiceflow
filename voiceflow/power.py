"""Detect whether the Mac is on AC power.

Used to keep transcription always-warm when plugged in (no battery cost) and
fall back to the user's chosen strategy on battery. Desktops report AC.
"""

from __future__ import annotations

import logging
import subprocess

log = logging.getLogger("voiceflow.power")


def on_ac_power() -> bool:
    try:
        out = subprocess.run(
            ["/usr/bin/pmset", "-g", "batt"],
            capture_output=True,
            text=True,
            timeout=2,
        ).stdout
        # "Now drawing from 'AC Power'" vs "'Battery Power'".
        return "AC Power" in out
    except Exception:
        log.exception("power check failed; assuming AC")
        return True  # favor performance if we can't tell
