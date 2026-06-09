"""First-run permissions onboarding window.

Shows the three permissions VoiceFlow needs, with a live status dot for each,
one-click buttons to the right System Settings pane, and auto-detection as the
user grants them. Offers a Quit & Relaunch once everything is granted (TCC
changes apply cleanly to a fresh process).
"""

from __future__ import annotations

import logging

import objc
from AppKit import (
    NSApp,
    NSButton,
    NSColor,
    NSFont,
    NSMakeRect,
    NSTextField,
    NSView,
    NSWindow,
    NSBackingStoreBuffered,
    NSWindowStyleMaskClosable,
    NSWindowStyleMaskTitled,
    NSWorkspace,
)
from Foundation import NSURL, NSBundle, NSObject, NSTimer

log = logging.getLogger("voiceflow.onboarding")


def _audio_media_type():
    """AVMediaTypeAudio, or its literal value ("soun") if the pyobjc constant
    didn't load — which happens in a frozen/bundled app where the framework
    metadata isn't fully present. Without this, the mic request is a no-op.
    """
    try:
        from AVFoundation import AVMediaTypeAudio

        if AVMediaTypeAudio:
            return AVMediaTypeAudio
    except Exception:
        log.warning("AVMediaTypeAudio constant unavailable; falling back to literal 'soun'")
    return "soun"


# (settings-pane key, title, why)
ROWS = [
    ("microphone", "Microphone", "Record your voice"),
    ("accessibility", "Accessibility", "Type text into any app"),
    ("input", "Input Monitoring", "Detect your hotkey"),
]

_PANES = {
    "accessibility": "x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility",
    "input": "x-apple.systempreferences:com.apple.preference.security?Privacy_ListenEvent",
    "microphone": "x-apple.systempreferences:com.apple.preference.security?Privacy_Microphone",
}


# --- permission checks (return True/False; True == granted) --------------


def mic_ok() -> bool:
    try:
        from AVFoundation import AVCaptureDevice

        return AVCaptureDevice.authorizationStatusForMediaType_(_audio_media_type()) == 3
    except Exception:
        log.exception("mic_ok check failed")
        return False


def accessibility_ok() -> bool:
    try:
        from ApplicationServices import AXIsProcessTrusted

        return bool(AXIsProcessTrusted())
    except Exception:
        return False


def input_ok() -> bool:
    try:
        import Quartz

        return bool(Quartz.CGPreflightListenEventAccess())
    except Exception:
        return False


def status_ok(key: str) -> bool:
    return {"microphone": mic_ok, "accessibility": accessibility_ok, "input": input_ok}[key]()


def all_ok() -> bool:
    return mic_ok() and accessibility_ok() and input_ok()


def needed() -> bool:
    return not all_ok()


# --- request / open-settings actions -------------------------------------


def _open_pane(key: str):
    NSWorkspace.sharedWorkspace().openURL_(NSURL.URLWithString_(_PANES[key]))


def _request(key: str):
    try:
        if key == "microphone":
            from AVFoundation import AVCaptureDevice

            mtype = _audio_media_type()
            log.info("requesting microphone access (media type=%r)", mtype)
            AVCaptureDevice.requestAccessForMediaType_completionHandler_(
                mtype, lambda granted: log.info("microphone access granted=%s", bool(granted))
            )
        elif key == "accessibility":
            from ApplicationServices import (
                AXIsProcessTrustedWithOptions,
                kAXTrustedCheckOptionPrompt,
            )

            AXIsProcessTrustedWithOptions({kAXTrustedCheckOptionPrompt: True})
        elif key == "input":
            import Quartz

            Quartz.CGRequestListenEventAccess()
    except Exception:
        log.exception("permission request failed for %s", key)


W, H = 480, 392
_GREEN = (0.20, 0.78, 0.35)
_RED = (0.90, 0.27, 0.23)


class Onboarding(NSObject):
    """NSObject so it can be the target of button clicks and the recheck timer."""

    def init(self):
        self = objc.super(Onboarding, self).init()
        if self is None:
            return None
        self._relaunch_cb = None
        self.window = None
        self._dots = {}  # key -> status NSTextField
        self._buttons = {}  # key -> NSButton
        self._timer = None
        self._footer = None
        self._relaunch_btn = None
        return self

    # python-only configuration
    @objc.python_method
    def set_relaunch(self, cb):
        self._relaunch_cb = cb

    @objc.python_method
    def _label(self, text, x, y, w, h, size=13.0, bold=False, color=None):
        lbl = NSTextField.alloc().initWithFrame_(NSMakeRect(x, y, w, h))
        lbl.setStringValue_(text)
        lbl.setBezeled_(False)
        lbl.setDrawsBackground_(False)
        lbl.setEditable_(False)
        lbl.setSelectable_(False)
        font = NSFont.boldSystemFontOfSize_(size) if bold else NSFont.systemFontOfSize_(size)
        lbl.setFont_(font)
        if color:
            lbl.setTextColor_(NSColor.colorWithCalibratedRed_green_blue_alpha_(*color, 1.0))
        self.window.contentView().addSubview_(lbl)
        return lbl

    @objc.python_method
    def _build(self):
        win = NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
            NSMakeRect(0, 0, W, H),
            NSWindowStyleMaskTitled | NSWindowStyleMaskClosable,
            NSBackingStoreBuffered,
            False,
        )
        win.setTitle_("VoiceFlow Setup")
        win.setReleasedWhenClosed_(False)
        win.center()
        win.setDelegate_(self)
        self.window = win

        self._label("Welcome to VoiceFlow 🎙️", 24, H - 52, W - 48, 28, size=20, bold=True)
        self._label(
            "Grant these three permissions so VoiceFlow can hear your hotkey, "
            "transcribe, and type for you.",
            24,
            H - 92,
            W - 48,
            34,
            size=12,
        )

        row_h = 64
        top = H - 130
        for i, (key, title, why) in enumerate(ROWS):
            y = top - i * row_h
            dot = self._label("⚪", 24, y - 26, 26, 24, size=18)
            self._dots[key] = dot
            self._label(title, 54, y - 14, 220, 20, size=14, bold=True)
            self._label(why, 54, y - 34, 240, 18, size=11)
            btn = NSButton.alloc().initWithFrame_(NSMakeRect(W - 150, y - 30, 126, 30))
            btn.setTitle_("Enable")
            btn.setBezelStyle_(1)  # rounded
            btn.setTarget_(self)
            btn.setAction_(objc.selector(self.enable_, signature=b"v@:@"))
            btn.setTag_(i)
            self.window.contentView().addSubview_(btn)
            self._buttons[key] = btn

        self._footer = self._label("", 24, 58, W - 48, 20, size=12, bold=True)

        relaunch = NSButton.alloc().initWithFrame_(NSMakeRect(W - 220, 16, 196, 32))
        relaunch.setTitle_("Quit & Relaunch")
        relaunch.setBezelStyle_(1)
        relaunch.setTarget_(self)
        relaunch.setAction_(objc.selector(self.relaunch_, signature=b"v@:@"))
        relaunch.setKeyEquivalent_("\r")
        self.window.contentView().addSubview_(relaunch)
        self._relaunch_btn = relaunch

        close = NSButton.alloc().initWithFrame_(NSMakeRect(24, 16, 110, 32))
        close.setTitle_("Later")
        close.setBezelStyle_(1)
        close.setTarget_(self)
        close.setAction_(objc.selector(self.later_, signature=b"v@:@"))
        self.window.contentView().addSubview_(close)

    @objc.python_method
    def show(self):
        if self.window is None:
            self._build()
        self._refresh()
        NSApp.activateIgnoringOtherApps_(True)
        self.window.makeKeyAndOrderFront_(None)
        self.window.orderFrontRegardless()
        if self._timer is None:
            self._timer = NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
                1.0, self, objc.selector(self.recheck_, signature=b"v@:@"), None, True
            )

    @objc.python_method
    def _refresh(self):
        for key, dot in self._dots.items():
            ok = status_ok(key)
            dot.setStringValue_("🟢" if ok else "🔴")
            self._buttons[key].setEnabled_(not ok)
            self._buttons[key].setTitle_("Enabled" if ok else "Enable")
        if all_ok():
            self._footer.setTextColor_(
                NSColor.colorWithCalibratedRed_green_blue_alpha_(*_GREEN, 1.0)
            )
            self._footer.setStringValue_("✅ All set! Click Quit & Relaunch to start.")
        else:
            self._footer.setTextColor_(
                NSColor.colorWithCalibratedRed_green_blue_alpha_(*_RED, 1.0)
            )
            self._footer.setStringValue_(
                "Toggle VoiceFlow on in each list — this updates automatically."
            )

    # --- selector actions (called by AppKit) ----------------------------

    def enable_(self, sender):
        key = ROWS[sender.tag()][0]
        _request(key)
        _open_pane(key)

    def relaunch_(self, _sender):
        if self._relaunch_cb:
            self._relaunch_cb()

    def later_(self, _sender):
        self.window.close()

    def recheck_(self, _timer):
        self._refresh()

    def windowWillClose_(self, _notification):
        if self._timer is not None:
            self._timer.invalidate()
            self._timer = None


def make() -> "Onboarding":
    return Onboarding.alloc().init()


def relaunch_app():
    """Relaunch the running .app a moment after this process quits."""
    import subprocess

    import rumps

    path = NSBundle.mainBundle().bundlePath()
    subprocess.Popen(["/bin/sh", "-c", f"sleep 1; open '{path}'"])
    rumps.quit_application()
