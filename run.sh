#!/usr/bin/env bash
# Launch VoiceFlow. Loads ./ .env if present (for GROQ_API_KEY).
set -euo pipefail
cd "$(dirname "$0")"

if [ -f .env ]; then
  set -a; . ./.env; set +a
fi

exec uv run voiceflow
