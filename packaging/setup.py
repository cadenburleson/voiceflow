"""Build a standalone VoiceFlow.app (menu-bar app) via py2app.

Built in ALIAS mode (`-A`): the bundle references the project venv in place
rather than copying ~2GB of native libraries. Fast, reliable, and gives the
app its own identity so macOS permissions attach to "VoiceFlow" instead of
your terminal. Local use only — don't move/delete the venv after building.

This lives in its own directory (no pyproject.toml here) on purpose: setuptools
would otherwise read the project's [project].dependencies into install_requires,
which py2app rejects.

    cd packaging && uv run python setup.py py2app -A
"""

from setuptools import setup

APP = ["app_main.py"]

OPTIONS = {
    "argv_emulation": False,
    "plist": {
        "CFBundleName": "VoiceFlow",
        "CFBundleDisplayName": "VoiceFlow",
        "CFBundleIdentifier": "com.cadenburleson.voiceflow",
        "CFBundleVersion": "0.1.0",
        "CFBundleShortVersionString": "0.1.0",
        "LSUIElement": True,  # menu-bar only, no Dock icon
        "NSMicrophoneUsageDescription": "VoiceFlow records your voice to transcribe it into text.",
        "NSHighResolutionCapable": True,
    },
}

setup(
    app=APP,
    name="VoiceFlow",
    options={"py2app": OPTIONS},
)
