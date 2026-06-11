"""Local transcription via parakeet-mlx (Apple Silicon).

Two paths:

- Streaming (default): a StreamSession is opened when recording starts and mic
  audio is fed to the model in fixed-size chunks while the user is still
  speaking. On hotkey release only the buffered tail remains to process, so
  insertion latency is near-constant regardless of dictation length. Fixed
  chunk sizes keep MLX on a small set of compiled graphs.

- Batch (fallback, and `streaming = false` in config): the whole clip in one
  generate() call. We feed the in-memory mic audio straight to the model,
  skipping parakeet's file path (temp WAV + ffmpeg subprocess). Audio is
  padded to 2-second buckets so repeated lengths reuse compiled graphs —
  though a long dictation can still hit a new bucket and pay a multi-second
  graph compile, which is why streaming is the default.
"""

from __future__ import annotations

import logging
import time

import mlx.core as mx
import numpy as np
from parakeet_mlx.audio import get_logmel

log = logging.getLogger("voiceflow.transcribe")

_model = None
_model_key: str | None = None

# Weight quantization (set from config at startup). 8 measured ~10-15% faster
# than bf16 with zero word diffs on test clips; 4-bit was SLOWER (dequant
# overhead beats the bandwidth savings at this model size). 0 = full bf16.
QUANT_BITS = 0
QUANT_GROUP = 64

BUCKET_SECONDS = 2.0

# Keep-warm runs constantly while idle; use a small input so each nudge is
# cheap (it only needs to keep the GPU clocked and Metal resident).
KEEP_WARM_SECONDS = 0.5

# Streaming: chunk size fed to the model while recording, and the local
# attention window (left, right) in encoder frames (~80ms each). The right
# context bounds how much mel history is re-encoded per chunk, i.e. the
# steady per-chunk cost. depth = how many encoder layers carry exact KV cache
# across chunks. (256, 256)/depth 2 measured word-identical to batch output
# on test clips; smaller right context or depth 1 dropped/garbled words near
# chunk boundaries.
CHUNK_SECONDS = 2.0
STREAM_CONTEXT = (256, 256)
STREAM_DEPTH = 2

# Final-flush skip: if everything still buffered at hotkey release peaks below
# this, it's room tone, not speech — don't pay a whole inference for it.
# (Logged speech peaks are 0.6+; keep a wide margin so words are never lost.)
SILENCE_PEAK = 0.008


def _quantize(model, bits: int) -> None:
    """Quantize the FFN/decoder linear layers in place.

    Attention layers stay bf16: streaming's local-attention swap rebuilds them
    as plain nn.Linear and can't load quantized weights.
    """
    import mlx.nn as nn

    def pred(path, m):
        return (
            isinstance(m, nn.Linear)
            and m.weight.shape[-1] % QUANT_GROUP == 0
            and "self_attn" not in path
        )

    nn.quantize(model, group_size=QUANT_GROUP, bits=bits, class_predicate=pred)


def load_model(name: str):
    """Load (and cache) the MLX model. Downloads from HF on first use."""
    global _model, _model_key
    key = f"{name}#q{QUANT_BITS}"
    if _model is None or _model_key != key:
        from parakeet_mlx import from_pretrained

        model = from_pretrained(name)
        if QUANT_BITS:
            try:
                _quantize(model, QUANT_BITS)
                log.info("model quantized to %d-bit (attention kept bf16)", QUANT_BITS)
            except Exception:
                log.exception("quantization failed — continuing in bf16")
        _model = model
        _model_key = key
    return _model


def _bucketed(audio: np.ndarray, samplerate: int) -> np.ndarray:
    """Pad with trailing silence to the next 2s boundary for stable shapes."""
    bucket = int(BUCKET_SECONDS * samplerate)
    n = audio.shape[0]
    target = ((n // bucket) + 1) * bucket
    if target > n:
        return np.concatenate([audio, np.zeros(target - n, dtype=audio.dtype)])
    return audio


def _run(model, audio: np.ndarray) -> str:
    mel = get_logmel(mx.array(audio), model.preprocessor_config)
    result = model.generate(mel)[0]
    text = getattr(result, "text", None)
    return (text if text is not None else str(result)).strip()


def transcribe(audio: np.ndarray, samplerate: int, model_name: str) -> str:
    if audio.size == 0:
        return ""
    model = load_model(model_name)
    expected = model.preprocessor_config.sample_rate
    if samplerate != expected:
        log.warning("samplerate %d != model %d; transcription may be wrong", samplerate, expected)
    return _run(model, _bucketed(audio, samplerate))


class StreamSession:
    """A live transcription session fed incrementally while recording.

    MLX's Metal stream is thread-local: create, feed, and finish a session on
    the same (single) MLX worker thread as all other model work.
    """

    def __init__(self, model_name: str):
        self._model = load_model(model_name)
        self._sr = self._model.preprocessor_config.sample_rate
        self._chunk = int(CHUNK_SECONDS * self._sr)
        self._pending: list[np.ndarray] = []
        self._pending_n = 0
        self.consumed = 0  # total samples accepted via add()/finish()
        self._stream = self._model.transcribe_stream(
            context_size=STREAM_CONTEXT, depth=STREAM_DEPTH
        ).__enter__()
        self._closed = False

    def add(self, audio: np.ndarray) -> None:
        """Buffer mic audio; runs the model once per full fixed-size chunk."""
        if audio.size == 0:
            return
        self.consumed += int(audio.size)
        self._pending.append(audio)
        self._pending_n += int(audio.size)
        while self._pending_n >= self._chunk:
            buf = np.concatenate(self._pending) if len(self._pending) > 1 else self._pending[0]
            piece, rest = buf[: self._chunk], buf[self._chunk :]
            self._pending = [rest] if rest.size else []
            self._pending_n = int(rest.size)
            self._stream.add_audio(mx.array(piece))

    def finish(self, tail: np.ndarray) -> str:
        """Feed the final samples, flush the remainder (padded with silence to
        the fixed chunk shape so no new graph is compiled), and return the
        transcript. The session is closed afterwards."""
        self.add(tail)
        if self._pending_n:
            buf = np.concatenate(self._pending) if len(self._pending) > 1 else self._pending[0]
            if float(np.max(np.abs(buf))) > SILENCE_PEAK:
                pad = self._chunk - buf.size
                if pad > 0:
                    buf = np.concatenate([buf, np.zeros(pad, dtype=buf.dtype)])
                self._stream.add_audio(mx.array(buf))
            else:
                log.info("final flush skipped: %.2fs of silence", buf.size / self._sr)
            self._pending, self._pending_n = [], 0
        text = self._stream.result.text
        self.close()
        return (text or "").strip()

    def close(self) -> None:
        """Restore the model's batch attention mode. Safe to call twice."""
        if not self._closed:
            self._closed = True
            try:
                self._stream.__exit__(None, None, None)
            except Exception:
                log.exception("stream close failed")


def warm_up(model_name: str, streaming: bool = True) -> None:
    """Preload the model and pre-compile the graph shapes the app will hit, so
    the first real dictations don't pay MLX's per-shape graph-compile cost.
    """
    model = load_model(model_name)
    sr = model.preprocessor_config.sample_rate
    try:
        _run(model, np.zeros(int(KEEP_WARM_SECONDS * sr), dtype=np.float32))
        if streaming:
            # First-chunk shape, steady-state shape, and the padded final flush.
            session = StreamSession(model_name)
            for _ in range(3):
                session.add(np.zeros(int(CHUNK_SECONDS * sr), dtype=np.float32))
            session.finish(np.zeros(sr // 2, dtype=np.float32))
        else:
            for seconds in (BUCKET_SECONDS, 2 * BUCKET_SECONDS, 3 * BUCKET_SECONDS):
                _run(model, np.zeros(int(seconds * sr), dtype=np.float32))
    except Exception:
        log.exception("warm-up inference failed")


def keep_warm(model_name: str) -> float:
    """A tiny inference to keep MLX/Metal resident and the GPU clocked up, so a
    dictation after a long idle gap is just as fast as one mid-session.

    Returns the elapsed milliseconds (a high value means the GPU had gone cold).
    """
    try:
        model = load_model(model_name)
        sr = model.preprocessor_config.sample_rate
        t0 = time.time()
        _run(model, np.zeros(int(KEEP_WARM_SECONDS * sr), dtype=np.float32))
        return (time.time() - t0) * 1000
    except Exception:
        log.exception("keep-warm failed")
        return 0.0
