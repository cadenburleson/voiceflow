# PyInstaller spec for a standalone, distributable VoiceFlow.app.
#
# Unlike the py2app alias build (local-only), this produces a self-contained
# bundle that runs on other Macs. Built with PyInstaller because the project's
# uv/standalone Python isn't a framework build (py2app can't package it).
#
#   cd packaging && ../.venv/bin/pyinstaller voiceflow.spec --noconfirm
#
# Heavy native deps are pulled in whole via collect_all so their data files
# ship too — most importantly mlx/lib/libmlx.dylib AND mlx/lib/mlx.metallib,
# which must land in the SAME directory (libmlx loads the metallib by a path
# relative to itself).

import os
from PyInstaller.utils.hooks import collect_all, collect_submodules

REPO_ROOT = os.path.dirname(SPECPATH)  # SPECPATH = the packaging/ dir

datas, binaries, hiddenimports = [], [], []
# AVFoundation/CoreMedia are collected whole so pyobjc's framework metadata
# ships too — without it, constants like AVMediaTypeAudio fail to load in the
# frozen app and the microphone-permission request silently no-ops.
for pkg in ("mlx", "parakeet_mlx", "rumps", "sounddevice", "soundfile", "soxr",
            "huggingface_hub", "AVFoundation", "CoreMedia"):
    d, b, h = collect_all(pkg)
    datas += d
    binaries += b
    hiddenimports += h

# pynput / pyobjc backends are imported dynamically.
hiddenimports += collect_submodules("pynput")
hiddenimports += ["objc", "Quartz", "AppKit", "Foundation", "PyObjCTools", "ApplicationServices"]

a = Analysis(
    ["app_main.py"],
    pathex=[REPO_ROOT],            # so `import voiceflow` resolves
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=["tkinter", "PyInstaller"],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="VoiceFlow",
    debug=False,
    strip=False,
    upx=False,
    console=False,            # menu-bar app, no terminal window
    target_arch="arm64",
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="VoiceFlow",
)

app = BUNDLE(
    coll,
    name="VoiceFlow.app",
    icon=os.path.join(REPO_ROOT, "assets", "icon", "VoiceFlow.icns"),
    bundle_identifier="com.cadenburleson.voiceflow",
    info_plist={
        "CFBundleName": "VoiceFlow",
        "CFBundleDisplayName": "VoiceFlow",
        "CFBundleShortVersionString": "0.2.0",
        "CFBundleVersion": "0.2.0",
        "LSUIElement": True,  # menu-bar only, no Dock icon
        "NSMicrophoneUsageDescription": "VoiceFlow records your voice to transcribe it into text.",
        "NSHighResolutionCapable": True,
    },
)
