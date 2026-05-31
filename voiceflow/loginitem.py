"""Launch-at-login support via a per-user LaunchAgent plist.

We use a LaunchAgent (not SMAppService) because the app is an ad-hoc-signed
local alias bundle — a plist in ~/Library/LaunchAgents is simple and reliable.
Writing the plist enables launch at the *next* login (launchd loads agents
then); we deliberately don't `launchctl load` it now, which would spawn a
duplicate of the already-running app.
"""

from __future__ import annotations

import logging
import plistlib
from pathlib import Path

from Foundation import NSBundle

log = logging.getLogger("voiceflow.loginitem")

LABEL = "com.cadenburleson.voiceflow"
PLIST = Path.home() / "Library" / "LaunchAgents" / f"{LABEL}.plist"


def _app_path() -> str:
    try:
        path = NSBundle.mainBundle().bundlePath()
    except Exception:
        path = None
    return path or "/Applications/VoiceFlow.app"


def is_enabled() -> bool:
    return PLIST.exists()


def set_enabled(enabled: bool) -> bool:
    try:
        if enabled:
            PLIST.parent.mkdir(parents=True, exist_ok=True)
            data = {
                "Label": LABEL,
                "ProgramArguments": ["/usr/bin/open", _app_path()],
                "RunAtLoad": True,
                "ProcessType": "Interactive",
            }
            with open(PLIST, "wb") as f:
                plistlib.dump(data, f)
        else:
            PLIST.unlink(missing_ok=True)
    except Exception:
        log.exception("failed to %s login item", "enable" if enabled else "disable")
    return is_enabled()
