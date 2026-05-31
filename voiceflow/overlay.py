"""Floating recording/transcribing indicator — the Wispr-style pill.

A borderless, non-activating NSPanel pinned near the bottom-center of the main
screen. It never takes focus (so paste still lands in your target app) and
shows a live mic-level meter while recording.

IMPORTANT: every public method touches AppKit and must be called on the main
thread. The app drives it from rumps' main-thread run loop.
"""

from __future__ import annotations

import Quartz
from AppKit import (
    NSColor,
    NSMakeRect,
    NSPanel,
    NSScreen,
    NSTextField,
    NSView,
    NSBackingStoreBuffered,
    NSWindowCollectionBehaviorCanJoinAllSpaces,
    NSWindowCollectionBehaviorFullScreenAuxiliary,
    NSWindowCollectionBehaviorStationary,
    NSWindowStyleMaskBorderless,
    NSWindowStyleMaskNonactivatingPanel,
)

_W, _H = 240.0, 52.0
_PAD = 16.0
_BAR_MAX = 120.0  # max width of the level bar in points


class Overlay:
    def __init__(self):
        self.panel = None
        self.label = None
        self.bar = None
        self._build()

    def _build(self):
        screen = NSScreen.mainScreen().frame()
        x = (screen.size.width - _W) / 2.0
        y = 120.0
        rect = NSMakeRect(x, y, _W, _H)

        style = NSWindowStyleMaskBorderless | NSWindowStyleMaskNonactivatingPanel
        panel = NSPanel.alloc().initWithContentRect_styleMask_backing_defer_(
            rect, style, NSBackingStoreBuffered, False
        )
        panel.setOpaque_(False)
        panel.setBackgroundColor_(NSColor.clearColor())
        panel.setLevel_(Quartz.kCGStatusWindowLevel)
        panel.setIgnoresMouseEvents_(True)
        panel.setHidesOnDeactivate_(False)
        panel.setBecomesKeyOnlyIfNeeded_(True)
        panel.setCollectionBehavior_(
            NSWindowCollectionBehaviorCanJoinAllSpaces
            | NSWindowCollectionBehaviorFullScreenAuxiliary
            | NSWindowCollectionBehaviorStationary
        )

        content = NSView.alloc().initWithFrame_(NSMakeRect(0, 0, _W, _H))
        content.setWantsLayer_(True)
        content.layer().setCornerRadius_(_H / 2.0)
        content.layer().setBackgroundColor_(
            NSColor.colorWithCalibratedWhite_alpha_(0.08, 0.92).CGColor()
        )
        panel.setContentView_(content)

        # Level / status bar (an expanding accent pill behind the label baseline)
        bar = NSView.alloc().initWithFrame_(NSMakeRect(_PAD, 12.0, 6.0, 6.0))
        bar.setWantsLayer_(True)
        bar.layer().setCornerRadius_(3.0)
        bar.layer().setBackgroundColor_(
            NSColor.colorWithCalibratedRed_green_blue_alpha_(0.36, 0.74, 1.0, 1.0).CGColor()
        )
        content.addSubview_(bar)

        label = NSTextField.alloc().initWithFrame_(
            NSMakeRect(_PAD, 22.0, _W - 2 * _PAD, 22.0)
        )
        label.setStringValue_("")
        label.setBezeled_(False)
        label.setDrawsBackground_(False)
        label.setEditable_(False)
        label.setSelectable_(False)
        label.setTextColor_(NSColor.whiteColor())
        from AppKit import NSFont

        label.setFont_(NSFont.systemFontOfSize_(13.0))
        content.addSubview_(label)

        panel.orderOut_(None)
        self.panel, self.label, self.bar = panel, label, bar

    # --- main-thread API -------------------------------------------------

    def show_listening(self):
        self.label.setStringValue_("Listening…")
        self._set_bar(0.0)
        self.panel.orderFrontRegardless()

    def show_transcribing(self):
        self.label.setStringValue_("Transcribing…")
        self._set_bar(1.0, dim=True)

    def show_message(self, text: str):
        self.label.setStringValue_(text)
        self._set_bar(0.0, dim=True)
        self.panel.orderFrontRegardless()

    def set_level(self, level: float):
        self._set_bar(level)

    def hide(self):
        self.panel.orderOut_(None)

    def _set_bar(self, level: float, dim: bool = False):
        width = 6.0 + max(0.0, min(1.0, level)) * _BAR_MAX
        frame = self.bar.frame()
        frame.size.width = width
        self.bar.setFrame_(frame)
        color = (
            NSColor.colorWithCalibratedWhite_alpha_(0.6, 1.0)
            if dim
            else NSColor.colorWithCalibratedRed_green_blue_alpha_(0.36, 0.74, 1.0, 1.0)
        )
        self.bar.layer().setBackgroundColor_(color.CGColor())
