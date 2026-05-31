"""Optional AI cleanup of the raw transcript via Groq's OpenAI-compatible API.

Fixes punctuation/casing and strips filler words. Fails open: any error (no
key, network, timeout) returns the original text so dictation never breaks.
"""

from __future__ import annotations

import requests

_ENDPOINT = "https://api.groq.com/openai/v1/chat/completions"

_SYSTEM = (
    "You clean up dictated speech-to-text. Fix punctuation, capitalization, and "
    "obvious transcription slips. Remove filler words (um, uh, like, you know). "
    "Keep the user's wording and meaning; do NOT answer questions, add commentary, "
    "or wrap the text in quotes. Output ONLY the cleaned text."
)


def clean(text: str, api_key: str, model: str, timeout: float = 8.0) -> str:
    if not api_key or not text.strip():
        return text
    try:
        resp = requests.post(
            _ENDPOINT,
            headers={"Authorization": f"Bearer {api_key}"},
            json={
                "model": model,
                "temperature": 0,
                "messages": [
                    {"role": "system", "content": _SYSTEM},
                    {"role": "user", "content": text},
                ],
            },
            timeout=timeout,
        )
        resp.raise_for_status()
        out = resp.json()["choices"][0]["message"]["content"].strip()
        return out or text
    except Exception:
        return text
