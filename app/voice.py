"""Turning your Telegram voice notes into text (Gemini, checked against Google's docs Oct 2026).

You can speak English, Hindi or Hinglish; the agent writes it down and then
treats it exactly like a typed message.
"""

import base64
import logging

import requests

from app.brands import Brand
from app.gemini import API

log = logging.getLogger(__name__)
DEFAULT_MODEL = "gemini-3.8-flash"
MAX_SECONDS = 180


class VoiceError(Exception):
    pass


def transcribe(brand: Brand, audio: bytes, mime_type: str = "audio/ogg") -> str:
    key = brand.secret("GEMINI_API_KEY")
    if not key:
        raise VoiceError("Voice notes need Gemini connected (Connect accounts → Gemini). You can type instead.")
    model = (brand.config.get("ai_voice") or {}).get("model", DEFAULT_MODEL)
    prompt = ("Transcribe exactly what the speaker says. They run an innerwear brand and are giving instructions to "
              "their social media assistant. They may speak English, Hindi or a mix (Hinglish): write Hindi words in "
              "Roman letters as Hinglish. Keep product names as spoken. Return only the transcript, nothing else. "
              "If there is no speech, return exactly: (no speech)")
    body = {"model": model, "input": [{"type": "text", "text": prompt},
                                      {"type": "audio", "mime_type": mime_type, "data": base64.b64encode(audio).decode()}]}
    try:
        r = requests.post(API, json=body, headers={"x-goog-api-key": key}, timeout=120)
    except requests.RequestException:
        raise VoiceError("Couldn't reach Gemini to understand the voice note. Try again, or type it.") from None
    payload = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
    if r.status_code != 200:
        msg = (payload.get("error") or {}).get("message", "")[:150]
        raise VoiceError(f"Gemini couldn't read the voice note (error {r.status_code}). {msg}".strip())
    steps = payload.get("steps")
    outputs = [s for s in steps if s.get("type") == "model_output"] if isinstance(steps, list) else [payload]
    text = " ".join(_texts(outputs)).strip()
    if not text or text == "(no speech)":
        raise VoiceError("I couldn't hear any words in that voice note. Try again a bit closer to the phone.")
    log.info("Voice note transcribed (%d characters)", len(text))
    return text


def _texts(node) -> list[str]:
    out = []
    if isinstance(node, dict):
        if node.get("type") == "text" and isinstance(node.get("text"), str):
            out.append(node["text"])
        for key in ("content", "parts", "candidates", "steps"):
            if key in node:
                out += _texts(node[key])
    elif isinstance(node, list):
        for child in node:
            out += _texts(child)
    return out
