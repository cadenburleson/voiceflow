# VoiceFlow

A fast, **local** push-to-talk dictation tool for macOS — a Wispr Flow-style
workflow you own. Hold a key, speak, release: your words are transcribed
on-device and typed into whatever app you're using.

- **Local transcription** via [`parakeet-mlx`](https://github.com/senstella/parakeet-mlx)
  on Apple Silicon — no per-use cost, no network round-trip, private.
- **Optional AI cleanup** (punctuation, casing, filler-word removal) via Groq's
  free, very fast `llama-3.1-8b-instant`. Skipped automatically if no key is set.
- **Menu-bar app** with a floating recording pill (live mic-level meter), just
  like Wispr.
- **Universal insertion** — pastes into any app via the clipboard + ⌘V.

Requires Apple Silicon (M-series) and macOS 13.5+.

## Download

**[⬇️ Download the latest VoiceFlow.dmg](https://github.com/cadenburleson/voiceflow/releases/latest)**

Open the `.dmg`, drag **VoiceFlow** into **Applications**, and double-click it —
no Python or developer tools needed. The app is signed and notarized by Apple,
so it just opens (no Gatekeeper warning).

On first run, grant **Microphone**, **Accessibility**, and **Input Monitoring**
when prompted, then choose your hotkey from the 🎙️ menu-bar icon. The
transcription model (~600 MB) downloads once on first use.

Prefer to run from source instead? See [Setup](#setup) below.

## Setup

```bash
cd voiceflow
uv sync                 # creates a 3.12 venv and installs deps
cp .env.example .env    # optional: add GROQ_API_KEY for AI cleanup
```

### Grant permissions (one time)

macOS gates microphone and keyboard control. On first run you'll be prompted;
if not, grant these to **your terminal app** (or the Python binary) and relaunch:

- **System Settings ▸ Privacy & Security ▸ Microphone** — for recording.
- **System Settings ▸ Privacy & Security ▸ Accessibility** — so VoiceFlow can
  read the global hotkey and send the ⌘V paste keystroke.

## Run

```bash
./run.sh
# or: uv run voiceflow
```

A 🎙️ icon appears in the menu bar (⏳ while the model loads the first time,
~a few seconds). Then:

1. **Hold Right-Option (⌥)** — a "Listening…" pill appears with a level meter.
2. **Speak.**
3. **Release** — it transcribes, optionally cleans up, and types the text
   wherever your cursor is.

## Configuration

Edit `~/.config/voiceflow/config.toml` (created on first run):

| Key | Default | Notes |
|-----|---------|-------|
| `hotkey` | `alt_r` | pynput key name: `alt_r`, `cmd_r`, `f13`, … |
| `mode` | `push_to_talk` | or `toggle` (tap to start, tap to stop) |
| `model` | `mlx-community/parakeet-tdt-0.6b-v2` | any parakeet-mlx model |
| `cleanup` | `true` | needs `GROQ_API_KEY` to actually run |
| `cleanup_model` | `llama-3.1-8b-instant` | any Groq chat model |
| `show_overlay` | `true` | the floating pill |
| `restore_clipboard` | `true` | put your old clipboard back after pasting |
| `min_seconds` | `0.3` | ignore accidental sub-300ms taps |

### Hotkey ideas
- `alt_r` / `cmd_r` — a modifier you rarely use alone (default).
- `f13`–`f20` — dedicated keys if your keyboard has them; zero conflicts.

## How it works

```
hold hotkey ─▶ sounddevice mic capture ─▶ parakeet-mlx (local STT)
                                              │
                              optional Groq cleanup
                                              │
                        clipboard + ⌘V into the focused app
```

The menu-bar app runs on the main thread (rumps); the hotkey listener and the
transcription worker run on background threads, and all UI updates are marshaled
back to the main thread. The overlay is a non-activating `NSPanel`, so pasting
still lands in your real target app.

## Troubleshooting

- **Hotkey does nothing / no paste** → Accessibility permission isn't granted to
  the process that launched it. Re-grant and relaunch.
- **No audio / mic error** → grant Microphone permission.
- **First dictation is slow** → that's the one-time model download + load; it's
  warm after that.
- **Cleanup not happening** → `GROQ_API_KEY` isn't set; the menu shows
  "AI cleanup: off".

## License

MIT — see [LICENSE](LICENSE). Use it, change it, ship it, sell it; just keep the
copyright and license notice. No warranty.
