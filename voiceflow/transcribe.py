"""Local transcription via parakeet-mlx (Apple Silicon).

We feed the in-memory mic audio straight to the model: compute the log-mel and
call generate(), skipping parakeet's file path entirely. That avoids a temp
WAV write AND an ffmpeg subprocess spawn per dictation (parakeet's load_audio
shells out to ffmpeg) — both slow and variable, the main source of "hiccups".

Audio is padded to fixed 2-second buckets so MLX sees a small set of repeating
input shapes and reuses its compiled graph instead of recompiling per length.
"""

from __future__ import annotations

import logging

import mlx.core as mx
import numpy as np
from parakeet_mlx.audio import get_logmel

log = logging.getLogger("voiceflow.transcribe")

_model = None
_model_name: str | None = None

BUCKET_SECONDS = 2.0


def load_model(name: str):
    """Load (and cache) the MLX model. Downloads from HF on first use."""
    global _model, _model_name
    if _model is None or _model_name != name:
        from parakeet_mlx import from_pretrained

        _model = from_pretrained(name)
        _model_name = name
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


def warm_up(model_name: str) -> None:
    """Preload the model and pre-compile the common bucket shapes so the first
    real dictations don't pay MLX's per-shape graph-compile cost.
    """
    model = load_model(model_name)
    sr = model.preprocessor_config.sample_rate
    try:
        for seconds in (BUCKET_SECONDS, 2 * BUCKET_SECONDS, 3 * BUCKET_SECONDS):
            _run(model, np.zeros(int(seconds * sr), dtype=np.float32))
    except Exception:
        log.exception("warm-up inference failed")
