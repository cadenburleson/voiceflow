"""The menu-bar app: hotkey -> record -> transcribe -> clean -> insert."""

from __future__ import annotations

import logging
import queue
import threading
import time

import numpy as np
import objc
import rumps
from AppKit import NSWorkspace, NSWorkspaceDidWakeNotification
from Foundation import NSObject, NSProcessInfo
from PyObjCTools import AppHelper
from pynput import keyboard

from . import audio, cleanup, config, loginitem, logsetup, onboarding, power, transcribe
from .audio import Recorder
from .config import Config
from .inserter import insert_text
from .overlay import Overlay

log = logging.getLogger("voiceflow.app")

IDLE = "🎙️"
RECORDING = "🔴"
WORKING = "✍️"
LOADING = "⏳"

# Keep MLX/Metal warm. The GPU clocks down ~5s after work, so nudge every few
# seconds to stay hot; relax to a long interval when we're letting it cool.
WARM_ACTIVE_INTERVAL = 2.0   # keep the GPU mostly hot during active use
WARM_IDLE_INTERVAL = 30.0    # relaxed (battery saver) — bounds deep-cold only
WARM_ACTIVE_WINDOW = 120.0   # "adaptive": stay hot this long after last use
COLD_AFTER = 5.0             # show "Warming up…" if the GPU has been idle longer
AC_RECHECK = 15.0            # re-check power source at most this often

WARM_PRESETS = [
    ("adaptive", "Adaptive (warm after recent use)"),
    ("always", "Always instant (more battery)"),
    ("battery_first", "Relaxed (battery saver)"),
]
WARM_LABELS = dict(WARM_PRESETS)

# NSActivityUserInitiated without the sleep-disable bit: prevents App Nap from
# throttling us while still letting the Mac sleep normally.
_ACTIVITY_OPTS = 0x00FFFFFF

# Common conflict-free triggers offered in the menu. (name, friendly label)
HOTKEY_PRESETS = [
    ("ctrl_l", "Left Control ⌃"),
    ("ctrl_r", "Right Control ⌃"),
    ("cmd_l", "Left Command ⌘"),
    ("cmd_r", "Right Command ⌘"),
    ("alt_l", "Left Option ⌥"),
    ("alt_r", "Right Option ⌥"),
    ("shift_r", "Right Shift ⇧"),
    ("f13", "F13"),
    ("f14", "F14"),
    ("f15", "F15"),
]
HOTKEY_LABELS = dict(HOTKEY_PRESETS)
MODE_PRESETS = [("push_to_talk", "Push to talk (hold)"), ("toggle", "Toggle (tap on/off)")]


def _make_matcher(name: str):
    special = getattr(keyboard.Key, name, None)
    if special is not None:
        # macOS often reports left modifiers as the generic key (e.g. Key.ctrl
        # instead of Key.ctrl_l), so for *_l names also match the generic.
        alts = {special}
        if name.endswith("_l"):
            generic = getattr(keyboard.Key, name[:-2], None)
            if generic is not None:
                alts.add(generic)
        return lambda k: k in alts
    return lambda k: isinstance(k, keyboard.KeyCode) and k.char == name


def _key_name(key) -> str | None:
    """The config name for a pressed key, or None if it can't be bound."""
    if isinstance(key, keyboard.Key):
        return key.name
    if isinstance(key, keyboard.KeyCode) and key.char:
        return key.char
    return None


class _WakeObserver(NSObject):
    """Calls a Python callback when the machine wakes from sleep.

    macOS disables the Quartz event tap behind the hotkey listener across
    sleep, so we rebuild the listener on wake.
    """

    def initWithCallback_(self, cb):
        self = objc.super(_WakeObserver, self).init()
        if self is None:
            return None
        self._cb = cb
        center = NSWorkspace.sharedWorkspace().notificationCenter()
        center.addObserver_selector_name_object_(
            self, objc.selector(self.onWake_, signature=b"v@:@"),
            NSWorkspaceDidWakeNotification, None,
        )
        return self

    def onWake_(self, _note):
        try:
            self._cb()
        except Exception:
            logging.getLogger("voiceflow.app").exception("wake handler failed")


class VoiceFlowApp(rumps.App):
    def __init__(self, cfg: Config):
        super().__init__("VoiceFlow", title=LOADING, quit_button=None)
        logsetup.setup()
        log.info("VoiceFlow starting; hotkey=%s mode=%s", cfg.hotkey, cfg.mode)
        self.cfg = cfg

        # Stop macOS App Nap from throttling the background app, so timers and
        # the keep-warm thread stay responsive after idle. Hold the token.
        self._activity = NSProcessInfo.processInfo().beginActivityWithOptions_reason_(
            _ACTIVITY_OPTS, "VoiceFlow low-latency dictation"
        )

        self.recorder = Recorder(cfg.samplerate, cfg.continuous_mic, cfg.preroll_seconds)
        self.overlay = Overlay() if cfg.show_overlay else None
        self._matches = _make_matcher(cfg.hotkey)

        self._recording = False
        self._busy = False  # transcribing/inserting — ignore the trigger
        self._capturing = False  # waiting to bind the next key press
        self._start_time = 0.0

        # keep-warm bookkeeping (monotonic clocks)
        self._last_activity = time.monotonic()  # last real dictation
        self._last_gpu = 0.0  # last GPU work (warm-up / keep-warm / transcribe)
        self._ac_cached = True
        self._ac_checked = 0.0

        self._level_timer = rumps.Timer(self._tick_level, 0.05)

        # --- menu --------------------------------------------------------
        self._status_item = rumps.MenuItem("Status: loading model…")
        self._hint_item = rumps.MenuItem(self._hint_text())

        hotkey_menu = rumps.MenuItem("Hotkey")
        hotkey_menu.add(rumps.MenuItem("Set by pressing a key…", callback=self._begin_capture))
        hotkey_menu.add(rumps.separator)
        self._hotkey_items: dict[str, rumps.MenuItem] = {}
        for name, label in HOTKEY_PRESETS:
            item = rumps.MenuItem(label, callback=self._on_pick_hotkey)
            self._hotkey_items[name] = item
            hotkey_menu.add(item)

        mode_menu = rumps.MenuItem("Mode")
        self._mode_items: dict[str, rumps.MenuItem] = {}
        for name, label in MODE_PRESETS:
            item = rumps.MenuItem(label, callback=self._on_pick_mode)
            self._mode_items[name] = item
            mode_menu.add(item)

        warm_menu = rumps.MenuItem("Speed / Warm-up")
        self._warm_items: dict[str, rumps.MenuItem] = {}
        for name, label in WARM_PRESETS:
            item = rumps.MenuItem(label, callback=self._on_pick_warm)
            self._warm_items[name] = item
            warm_menu.add(item)
        warm_menu.add(rumps.separator)
        self._ac_item = rumps.MenuItem("Always warm when plugged in", callback=self._toggle_warm_ac)
        self._ac_item.state = 1 if cfg.warm_on_ac else 0
        warm_menu.add(self._ac_item)

        self._login_item = rumps.MenuItem("Launch at Login", callback=self._toggle_login)
        self._login_item.state = 1 if loginitem.is_enabled() else 0

        self.menu = [
            self._status_item,
            None,
            self._hint_item,
            hotkey_menu,
            mode_menu,
            warm_menu,
            rumps.MenuItem(
                f"AI cleanup: {'on' if cfg.cleanup_enabled else 'off'}", callback=None
            ),
            None,
            self._login_item,
            rumps.MenuItem("Permissions & Setup…", callback=self._open_onboarding),
            rumps.MenuItem("Quit VoiceFlow", callback=rumps.quit_application),
        ]
        self._refresh_checks()

        self._onboarding = None

        # All MLX work happens on ONE dedicated thread. MLX's Metal GPU stream
        # is thread-local, so warm-up and every transcription must share a
        # thread — otherwise: "There is no Stream(gpu, 0) in current thread".
        self._jobs: queue.Queue = queue.Queue()
        threading.Thread(target=self._worker, daemon=True).start()

        # Global hotkey listener (needs Input Monitoring permission).
        self._listener = None
        self._start_listener()

        # In continuous mode, warm the mic now so the pre-roll buffer is
        # already filling before the first dictation (no clipped first word).
        if cfg.continuous_mic:
            try:
                self.recorder.ensure_open()
            except Exception:
                log.exception("initial mic warm-up failed (will retry on first use)")

        # macOS disables the event tap across sleep — rebuild it on wake.
        self._wake_observer = _WakeObserver.alloc().initWithCallback_(self._on_wake)

        # Show onboarding on first run if any permission is missing.
        AppHelper.callAfter(self._auto_onboard)

    # --- lifecycle -------------------------------------------------------

    def _start_listener(self):
        if self._listener is not None:
            try:
                self._listener.stop()
            except Exception:
                pass
        self._listener = keyboard.Listener(
            on_press=self._on_press, on_release=self._on_release
        )
        self._listener.start()

    def _on_wake(self):
        log.info("woke from sleep — restarting hotkey listener and refreshing audio")
        self._recording = False
        self._capturing = False
        self._start_listener()
        audio.reinit()
        self.recorder.reopen()

    def _toggle_login(self, sender):
        enabled = loginitem.set_enabled(not loginitem.is_enabled())
        sender.state = 1 if enabled else 0
        log.info("launch-at-login set to %s", enabled)

    # --- keep-warm strategy ---------------------------------------------

    def _on_ac(self) -> bool:
        now = time.monotonic()
        if now - self._ac_checked > AC_RECHECK:
            self._ac_cached = power.on_ac_power()
            self._ac_checked = now
        return self._ac_cached

    def _keepwarm_interval(self) -> float:
        """How often to nudge the GPU right now, per strategy + power state."""
        if self.cfg.warm_on_ac and self._on_ac():
            return WARM_ACTIVE_INTERVAL  # plugged in: keep it hot, no battery cost
        strategy = self.cfg.warm_strategy
        if strategy == "always":
            return WARM_ACTIVE_INTERVAL
        if strategy == "battery_first":
            return WARM_IDLE_INTERVAL
        # adaptive: hot for a window after recent use, then relax
        if time.monotonic() - self._last_activity < WARM_ACTIVE_WINDOW:
            return WARM_ACTIVE_INTERVAL
        return WARM_IDLE_INTERVAL

    def _on_pick_warm(self, sender):
        for name, item in self._warm_items.items():
            if item is sender:
                self.cfg.warm_strategy = name
                config.save(self.cfg)
                self._refresh_checks()
                return

    def _toggle_warm_ac(self, sender):
        self.cfg.warm_on_ac = not self.cfg.warm_on_ac
        sender.state = 1 if self.cfg.warm_on_ac else 0
        config.save(self.cfg)

    def _worker(self):
        """Single thread for all MLX work: warm up, then process dictations.

        When idle, periodically runs a tiny keep-warm inference so MLX/Metal
        stays hot, at a cadence set by the warm strategy and power state.
        """
        try:
            transcribe.warm_up(self.cfg.model)
            self._last_gpu = time.monotonic()
            AppHelper.callAfter(self._set_ready)
        except Exception as exc:  # surfaced in the menu, not fatal
            log.exception("model warm-up failed")
            AppHelper.callAfter(self._set_status, f"Status: model error — {exc}")
        while True:
            try:
                audio = self._jobs.get(timeout=self._keepwarm_interval())
            except queue.Empty:
                if not self._busy and not self._recording:
                    ms = transcribe.keep_warm(self.cfg.model)
                    self._last_gpu = time.monotonic()
                    if ms > 700:  # only flag anomalously deep cold; ~450ms is normal
                        log.info("keep-warm unusually slow: %.0f ms", ms)
                continue
            if audio is None:
                continue
            self._do_transcribe(audio)

    def _set_ready(self):
        self.title = IDLE
        self._status_item.title = "Status: ready"

    def _set_status(self, text: str):
        self._status_item.title = text

    # --- onboarding ------------------------------------------------------

    def _show_onboarding(self):
        if self._onboarding is None:
            self._onboarding = onboarding.make()
            self._onboarding.set_relaunch(onboarding.relaunch_app)
        self._onboarding.show()

    def _auto_onboard(self):
        if onboarding.needed():
            self._show_onboarding()

    def _open_onboarding(self, _sender):
        self._show_onboarding()

    # --- hotkey config / menu -------------------------------------------

    def _hint_text(self) -> str:
        verb = "Hold" if self.cfg.mode == "push_to_talk" else "Tap"
        label = HOTKEY_LABELS.get(self.cfg.hotkey, self.cfg.hotkey)
        return f"{verb} [{label}] to talk"

    def _refresh_checks(self):
        for name, item in self._hotkey_items.items():
            item.state = 1 if name == self.cfg.hotkey else 0
        for name, item in self._mode_items.items():
            item.state = 1 if name == self.cfg.mode else 0
        for name, item in self._warm_items.items():
            item.state = 1 if name == self.cfg.warm_strategy else 0
        self._hint_item.title = self._hint_text()

    def _apply_hotkey(self, name: str):
        self.cfg.hotkey = name
        self._matches = _make_matcher(name)
        config.save(self.cfg)
        self._refresh_checks()

    def _on_pick_hotkey(self, sender):
        for name, item in self._hotkey_items.items():
            if item is sender:
                self._apply_hotkey(name)
                return

    def _on_pick_mode(self, sender):
        for name, item in self._mode_items.items():
            if item is sender:
                self.cfg.mode = name
                config.save(self.cfg)
                self._refresh_checks()
                return

    def _begin_capture(self, _sender):
        if self._busy or self._recording:
            return
        self._capturing = True
        if self.overlay:
            self.overlay.show_message("Press your hotkey…")

    def _finish_capture(self, name: str):
        self._apply_hotkey(name)
        if self.overlay:
            self.overlay.hide()

    # --- hotkey handling -------------------------------------------------

    def _on_press(self, key):
        if self._capturing:
            name = _key_name(key)
            if name:
                self._capturing = False
                AppHelper.callAfter(self._finish_capture, name)
            return
        if not self._matches(key) or self._busy:
            return
        if self.cfg.mode == "toggle":
            if self._recording:
                self._stop()
            else:
                self._start()
        else:  # push_to_talk
            if not self._recording:
                self._start()

    def _on_release(self, key):
        if self.cfg.mode != "toggle" and self._matches(key) and self._recording:
            self._stop()

    # --- recording -------------------------------------------------------

    def _start(self):
        self._recording = True
        self._start_time = time.time()
        try:
            self.recorder.start()
        except Exception as exc:
            self._recording = False
            log.exception("mic start failed")
            AppHelper.callAfter(self._set_status, f"Status: mic error — {exc}")
            return
        log.info("recording started")
        AppHelper.callAfter(self._enter_listening_ui)

    def _enter_listening_ui(self):
        self.title = RECORDING
        if self.overlay:
            self.overlay.show_listening()
        self._level_timer.start()

    def _stop(self):
        self._recording = False
        audio = self.recorder.stop()
        AppHelper.callAfter(self._level_timer.stop)
        duration = time.time() - self._start_time

        rms = float(np.sqrt(np.mean(audio**2))) if audio.size else 0.0
        log.info(
            "stop: duration=%.2fs samples=%d rms=%.5f (rms~0 means no mic audio)",
            duration,
            audio.size,
            rms,
        )

        if duration < self.cfg.min_seconds or audio.size == 0:
            log.info("stop: too short (<%.2fs) or empty — discarding", self.cfg.min_seconds)
            AppHelper.callAfter(self._reset_ui)
            return

        self._busy = True
        AppHelper.callAfter(self._enter_working_ui)
        self._jobs.put(audio)

    def _enter_working_ui(self):
        self.title = WORKING
        if self.overlay:
            cold = (time.monotonic() - self._last_gpu) > COLD_AFTER
            if cold:
                self.overlay.show_message("Warming up…")
            else:
                self.overlay.show_transcribing()

    def _save_fail_clip(self, audio):
        try:
            import wave

            pcm = (np.clip(audio, -1.0, 1.0) * 32767).astype("<i2")
            with wave.open("/tmp/voiceflow_fail.wav", "wb") as w:
                w.setnchannels(1)
                w.setsampwidth(2)
                w.setframerate(self.cfg.samplerate)
                w.writeframes(pcm.tobytes())
        except Exception:
            log.exception("fail-clip save failed")

    def _do_transcribe(self, audio):
        try:
            peak = float(np.max(np.abs(audio))) if audio.size else 0.0
            rms = float(np.sqrt(np.mean(audio**2))) if audio.size else 0.0
            t0 = time.time()
            text = transcribe.transcribe(audio, self.cfg.samplerate, self.cfg.model)
            log.info(
                "transcribed %d chars in %.0f ms (%.2fs audio, peak=%.3f rms=%.4f)",
                len(text), (time.time() - t0) * 1000, audio.size / self.cfg.samplerate, peak, rms,
            )
            if not text.strip() and audio.size:
                self._save_fail_clip(audio)
                log.warning("EMPTY transcription — saved /tmp/voiceflow_fail.wav (peak=%.3f rms=%.4f)", peak, rms)
            if text and self.cfg.cleanup_enabled:
                text = cleanup.clean(text, self.cfg.groq_key, self.cfg.cleanup_model)
                log.info("cleaned up -> %d chars", len(text))
            if text:
                insert_text(text, restore_clipboard=self.cfg.restore_clipboard)
            else:
                log.info("no text to insert")
        except Exception as exc:
            log.exception("process error")
            AppHelper.callAfter(self._set_status, f"Status: error — {exc}")
        finally:
            self._busy = False
            now = time.monotonic()
            self._last_gpu = now       # GPU just ran
            self._last_activity = now  # real use → adaptive stays warm
            AppHelper.callAfter(self._reset_ui)

    def _reset_ui(self):
        self.title = IDLE
        if self.overlay:
            self.overlay.hide()

    # --- meter -----------------------------------------------------------

    def _tick_level(self, _timer):
        if self.overlay and self._recording:
            self.overlay.set_level(self.recorder.level)
