"""Configuration loading.

Reads ~/.config/voiceflow/config.toml (created on first run from defaults).
Environment variables override file values. GROQ_API_KEY enables AI cleanup.
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, asdict
from pathlib import Path

CONFIG_DIR = Path.home() / ".config" / "voiceflow"
CONFIG_PATH = CONFIG_DIR / "config.toml"

# Keys recognized by pynput for the push-to-talk trigger. See README for the list.
DEFAULT_HOTKEY = "alt_r"  # right Option/Alt key — hold to talk


@dataclass
class Config:
    # Trigger
    hotkey: str = DEFAULT_HOTKEY
    mode: str = "push_to_talk"  # "push_to_talk" (hold) or "toggle" (tap on/off)

    # Transcription (local, MLX)
    model: str = "mlx-community/parakeet-tdt-0.6b-v2"
    samplerate: int = 16000
    # Transcribe while you speak so the text is ready the moment you release
    # the hotkey. Set false to transcribe the whole clip after release instead.
    streaming: bool = True
    # Weight quantization: 8 is ~10-15% faster with no measured accuracy loss;
    # 0 disables (full bf16). 4 is not recommended (slower, more accuracy risk).
    quantize_bits: int = 8

    # Mic capture. Continuous keeps the stream warm with a rolling pre-roll
    # buffer so the start of speech is never clipped (macOS shows the mic dot).
    continuous_mic: bool = True
    preroll_seconds: float = 0.5

    # Keep-warm: the GPU clocks down after ~5s idle, making the next dictation
    # slower. Strategy used ON BATTERY: "adaptive" (warm only ~2min after recent
    # use), "always" (always warm), or "battery_first" (relaxed, saves power).
    # When plugged in, warm_on_ac forces always-warm regardless (no battery cost).
    warm_strategy: str = "adaptive"
    warm_on_ac: bool = True

    # AI cleanup (optional, via Groq's OpenAI-compatible API)
    cleanup: bool = True
    groq_api_key: str = ""
    cleanup_model: str = "llama-3.1-8b-instant"

    # Behaviour
    show_overlay: bool = True
    restore_clipboard: bool = True
    min_seconds: float = 0.3  # ignore accidental sub-300ms taps

    @property
    def groq_key(self) -> str:
        return os.environ.get("GROQ_API_KEY", self.groq_api_key).strip()

    @property
    def cleanup_enabled(self) -> bool:
        return self.cleanup and bool(self.groq_key)


def _render(cfg: Config) -> str:
    def b(v: bool) -> str:
        return str(v).lower()

    lines = [
        "# VoiceFlow configuration",
        "# Hold the hotkey, speak, release -> text is typed at your cursor.",
        "# (The hotkey and mode can also be changed live from the menu bar.)",
        "",
        f'hotkey = "{cfg.hotkey}"   # key name; e.g. fn (Function/Globe), alt_r, cmd_r, f13',
        f'mode = "{cfg.mode}"  # "push_to_talk" or "toggle"',
        "",
        f'model = "{cfg.model}"',
        f"samplerate = {cfg.samplerate}",
        "# streaming transcribes while you speak (fastest); false = after release.",
        f"streaming = {b(cfg.streaming)}",
        "# quantize_bits: 8 = faster/lighter (recommended), 0 = full precision.",
        f"quantize_bits = {cfg.quantize_bits}",
        "",
        "# continuous_mic keeps the mic warm with a rolling pre-roll buffer so the",
        "# first words aren't clipped (macOS shows the orange mic dot while running).",
        f"continuous_mic = {b(cfg.continuous_mic)}",
        f"preroll_seconds = {cfg.preroll_seconds}",
        "",
        '# warm_strategy (on battery): "adaptive" | "always" | "battery_first".',
        "# warm_on_ac forces always-warm while plugged in.",
        f'warm_strategy = "{cfg.warm_strategy}"',
        f"warm_on_ac = {b(cfg.warm_on_ac)}",
        "",
        "# AI cleanup fixes punctuation/casing and removes filler words.",
        "# Requires a Groq API key (free tier is fast + generous):",
        "#   set GROQ_API_KEY in your environment, or put it below.",
        f"cleanup = {b(cfg.cleanup)}",
        f'groq_api_key = "{cfg.groq_api_key}"',
        f'cleanup_model = "{cfg.cleanup_model}"',
        "",
        f"show_overlay = {b(cfg.show_overlay)}",
        f"restore_clipboard = {b(cfg.restore_clipboard)}",
        f"min_seconds = {cfg.min_seconds}",
        "",
    ]
    return "\n".join(lines)


def _write_default(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_render(Config()))


def save(cfg: Config) -> None:
    """Persist the current config back to disk (preserves the groq key)."""
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(_render(cfg))


def load() -> Config:
    if not CONFIG_PATH.exists():
        _write_default(CONFIG_PATH)
        return Config()
    with open(CONFIG_PATH, "rb") as f:
        data = tomllib.load(f)
    known = {k: v for k, v in data.items() if k in Config.__dataclass_fields__}
    return Config(**known)
