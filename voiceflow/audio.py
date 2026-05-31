"""Microphone capture.

Two modes:
- continuous (default): the input stream stays open and continuously fills a
  rolling buffer. While idle we keep only the last `preroll` seconds; on start()
  we stop trimming, so the returned audio includes a pre-roll from *before* the
  keypress — this is what prevents the first word from being clipped. macOS
  shows the mic-in-use indicator while the app runs; audio is never persisted.
- on-demand: the stream is opened on start() and closed on stop(). No mic dot
  at rest, but the first fraction of a second can clip while the device spins up.

Exposes a smoothed RMS `level` for the overlay meter.
"""

from __future__ import annotations

import logging
import threading
import time

import numpy as np
import sounddevice as sd

log = logging.getLogger("voiceflow.audio")


def reinit() -> None:
    """Refresh PortAudio's device list.

    PortAudio caches devices at init; after sleep/wake or a device change the
    cached handle goes stale and opening a stream fails with -9986. Tearing it
    down and re-initializing picks up the current default device.
    """
    try:
        sd._terminate()
        sd._initialize()
        log.info("reinitialized PortAudio")
    except Exception:
        log.exception("PortAudio reinit failed")


class Recorder:
    def __init__(self, samplerate: int = 16000, continuous: bool = True,
                 preroll_seconds: float = 0.5):
        self.samplerate = samplerate
        self.continuous = continuous
        self.preroll_samples = int(preroll_seconds * samplerate)
        self._stream: sd.InputStream | None = None
        self._frames: list[np.ndarray] = []
        self._lock = threading.Lock()
        self._active = False  # True while a dictation is being captured
        self.level = 0.0  # smoothed RMS in [0, 1], read by the overlay
        self._last_cb = 0.0  # monotonic time of the last audio callback

    # --- stream callback -------------------------------------------------

    def _callback(self, indata, frames, time_info, status):  # noqa: ARG002
        if status:
            log.warning("audio callback status: %s", status)
        self._last_cb = time.monotonic()
        chunk = indata.copy().reshape(-1)
        rms = float(np.sqrt(np.mean(chunk**2)) + 1e-9)
        target = min(1.0, rms * 8.0)
        self.level = 0.6 * self.level + 0.4 * target
        with self._lock:
            self._frames.append(chunk)
            if self.continuous and not self._active:
                self._trim_to_preroll_locked()

    def seconds_since_audio(self) -> float:
        if self._last_cb == 0.0:
            return float("inf")
        return time.monotonic() - self._last_cb

    def _trim_to_preroll_locked(self):
        total = sum(f.shape[0] for f in self._frames)
        while len(self._frames) > 1 and total - self._frames[0].shape[0] >= self.preroll_samples:
            total -= self._frames.pop(0).shape[0]

    # --- stream lifecycle ------------------------------------------------

    def _open(self) -> sd.InputStream:
        stream = sd.InputStream(
            samplerate=self.samplerate,
            channels=1,
            dtype="float32",
            blocksize=1024,
            latency="low",
            callback=self._callback,
        )
        stream.start()
        return stream

    def _open_with_retry(self) -> sd.InputStream:
        try:
            return self._open()
        except sd.PortAudioError:
            log.warning("InputStream open failed; reinitializing PortAudio and retrying")
            reinit()
            return self._open()

    def ensure_open(self) -> None:
        """Open the continuous stream if not already running (continuous mode)."""
        if self._stream is None:
            self._stream = self._open_with_retry()

    def close(self) -> None:
        if self._stream is not None:
            try:
                self._stream.stop()
                self._stream.close()
            except Exception:
                pass
            self._stream = None

    def reopen(self) -> None:
        """Rebuild the stream after sleep/wake."""
        self.close()
        with self._lock:
            self._frames = []
        if self.continuous:
            try:
                self.ensure_open()
            except Exception:
                log.exception("reopen failed")

    # --- record API ------------------------------------------------------

    def start(self) -> None:
        if self.continuous:
            self.ensure_open()
            # If the stream has gone quiet (CoreAudio glitch / device change),
            # the callback stops firing and the buffer is stale silence. Rebuild.
            stale = self.seconds_since_audio()
            if stale > 1.0:
                log.warning("mic stream stale (%.1fs since audio) — reopening", stale)
                self.reopen()
            with self._lock:
                self._active = True  # stop trimming; keep the pre-roll we have
        else:
            with self._lock:
                self._frames = []
            self.level = 0.0
            self._stream = self._open_with_retry()

    def stop(self) -> np.ndarray:
        if self.continuous:
            with self._lock:
                self._active = False
                data = (
                    np.concatenate(self._frames).astype(np.float32)
                    if self._frames
                    else np.zeros(0, dtype=np.float32)
                )
                # keep only the trailing pre-roll for the next capture
                self._frames = [data[-self.preroll_samples:].copy()] if data.size else []
            self.level = 0.0
            return data

        self.close()
        self.level = 0.0
        with self._lock:
            if not self._frames:
                return np.zeros(0, dtype=np.float32)
            return np.concatenate(self._frames).astype(np.float32)

    @property
    def is_recording(self) -> bool:
        return self._active if self.continuous else self._stream is not None
