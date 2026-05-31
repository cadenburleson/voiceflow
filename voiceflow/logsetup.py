"""File logging so a Finder-launched .app leaves a diagnosable trail.

Logs to ~/Library/Logs/voiceflow.log. Safe to call setup() repeatedly.
"""

from __future__ import annotations

import logging
import logging.handlers
from pathlib import Path

LOG_PATH = Path.home() / "Library" / "Logs" / "voiceflow.log"

_configured = False


def setup() -> Path:
    global _configured
    if _configured:
        return LOG_PATH
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    handler = logging.handlers.RotatingFileHandler(
        LOG_PATH, maxBytes=512_000, backupCount=2
    )
    handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    )
    root = logging.getLogger("voiceflow")
    root.setLevel(logging.INFO)
    root.handlers = [handler]
    root.propagate = False
    _configured = True
    return LOG_PATH
