"""Entry point: check permissions, then run the menu-bar app."""

from __future__ import annotations

import sys

from . import config
from .app import VoiceFlowApp


def _check_accessibility() -> bool:
    """Returns True if trusted; otherwise triggers the system prompt."""
    try:
        from ApplicationServices import (
            AXIsProcessTrustedWithOptions,
            kAXTrustedCheckOptionPrompt,
        )

        return bool(
            AXIsProcessTrustedWithOptions({kAXTrustedCheckOptionPrompt: True})
        )
    except Exception:
        return True  # can't check — let it run; pynput will error visibly if blocked


def main() -> int:
    cfg = config.load()

    if not _check_accessibility():
        print(
            "\n⚠️  VoiceFlow needs Accessibility permission to read the hotkey and "
            "paste text.\n"
            "    Grant it in System Settings ▸ Privacy & Security ▸ Accessibility "
            "for your terminal (or the Python binary), then relaunch.\n",
            file=sys.stderr,
        )

    print(f"VoiceFlow running. Hold [{cfg.hotkey}] to dictate. Config: {config.CONFIG_PATH}")
    if not cfg.cleanup_enabled and cfg.cleanup:
        print("AI cleanup is OFF (set GROQ_API_KEY to enable).")

    VoiceFlowApp(cfg).run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
