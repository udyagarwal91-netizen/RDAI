"""Reels: an AI video clip from Google's Gemini Omni Flash, finished by the agent.

1. Gemini makes a short vertical clip using the real product photo as the reference.
2. The agent lays its own layers on top with FFmpeg (bundled — nothing to install):
   the hook (first seconds), product + Shopify price (middle), an end card with the
   call to action (last seconds), and the logo throughout. The AI never writes text,
   so words and prices are always correct.
3. Three frames are pulled out for the quality check.

Model and price checked against Google's docs (Oct 2026): gemini-omni-1.1-flash,
about US$0.10 per second of 720p video. Veo 3.1 previews were shut down on 22 Oct 2026.
"""

import base64
import logging
import re
import subprocess
import tempfile
import time
from pathlib import Path

import requests

from app import renderer
from app.brands import Brand
from app.gemini import API, COMMON_RULES, STYLE_RULES

log = logging.getLogger(__name__)
DEFAULT_MODEL = "gemini-omni-1.1-flash"
PRICE_PER_SECOND = {"720p": 0.10, "1080p": 0.10}  # 1080p is an upscale of 720p
FILES_API = "https://generativelanguage.googleapis.com/v1beta/files"
REEL_SIZE = (1080, 1920)
ENCODE_PRESET = "medium"  # quality/speed balance for the final reel (tests use "ultrafast")


class VideoError(Exception):
    """A problem making a reel, with a message the owner can act on."""


def ffmpeg() -> str:
    import imageio_ffmpeg
    return imageio_ffmpeg.get_ffmpeg_exe()


# ---------------------------------------------------------------- 1. the AI clip

def generate_clip(brand: Brand, scene: str, style: str, reference: bytes, extra_instructions: str = "") -> tuple[bytes, float]:
    """Ask Gemini for a vertical clip. Returns (mp4 bytes, cost in US$). Takes 1–5 minutes."""
    key = brand.secret("GEMINI_API_KEY")
    if not key:
        raise VideoError("Gemini isn't connected yet. Go to Connect accounts → Gemini.")
    cfg = brand.config.get("ai_video") or {}
    model = cfg.get("model", DEFAULT_MODEL)
    resolution = cfg.get("resolution", "720p")
    prompt = (f"Create a short vertical (9:16) social media video for the brand {brand.name}. "
              f"The attached image is the product reference. Scene: {scene}\n\n"
              f"{STYLE_RULES.get(style, STYLE_RULES['product_only'])}\n\n{COMMON_RULES}\n"
              "Smooth, slow camera movement; steady and premium. No on-screen text, captions or subtitles. "
              "Soft, upbeat instrumental background music; no voice-over, no lyrics.")
    if extra_instructions:
        prompt += f"\n\nAlso: {extra_instructions}"
    body = {
        "model": model,
        "input": [{"type": "text", "text": prompt},
                  {"type": "image", "mime_type": "image/jpeg", "data": base64.b64encode(reference).decode()}],
        "response_format": {"type": "video", "aspect_ratio": "9:16", "resolution": resolution},
        "generation_config": {"video_config": {"task": "reference_to_video"}},
    }
    try:
        r = requests.post(API, json=body, headers={"x-goog-api-key": key}, timeout=900)
    except requests.RequestException:
        raise VideoError("Couldn't reach Gemini (or it took over 15 minutes). Try again later.") from None
    if r.status_code in (401, 403):
        raise VideoError("Gemini refused — video needs billing switched on for your Google account (Connect accounts → Gemini).")
    if r.status_code == 429:
        raise VideoError("Gemini's video limit was reached for now. Try again in an hour.")
    payload = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
    if r.status_code != 200:
        msg = (payload.get("error") or {}).get("message", "")[:200]
        raise VideoError(f"Gemini replied with error {r.status_code}. {msg}".strip())
    steps = payload.get("steps")
    outputs = [s for s in steps if s.get("type") == "model_output"] if isinstance(steps, list) else [payload]
    block = _find_video(outputs)
    if block is None:
        raise VideoError(f"Gemini didn't make a video ({payload.get('status') or 'no video returned'}) — often its "
                         "safety filter. Try a different style or scene.")
    clip = base64.b64decode(block["data"]) if block.get("data") else _download(block["uri"], key)
    seconds = duration(clip)
    cost = seconds * PRICE_PER_SECOND.get(resolution, 0.10)
    log.info("Gemini clip made with %s: %.1f s, about US$%.2f", model, seconds, cost)
    return clip, cost


def _find_video(node):
    if isinstance(node, dict):
        if node.get("type") == "video" and (node.get("data") or node.get("uri")):
            return node
        children = node.values()
    elif isinstance(node, list):
        children = node
    else:
        return None
    for child in children:
        found = _find_video(child)
        if found:
            return found
    return None


def _download(uri: str, key: str) -> bytes:
    """Videos delivered as a file link: wait until ready, then download."""
    file_id = uri.rstrip("/").split("/files/")[-1].split(":")[0]
    for _ in range(60):
        info = requests.get(f"{FILES_API}/{file_id}", headers={"x-goog-api-key": key}, timeout=30).json()
        if info.get("state") == "FAILED":
            raise VideoError("Gemini couldn't finish the video file. Try again.")
        if info.get("state") in (None, "ACTIVE"):
            break
        time.sleep(5)
    r = requests.get(f"{FILES_API}/{file_id}:download", params={"alt": "media"}, headers={"x-goog-api-key": key},
                     timeout=300)
    if r.status_code != 200:
        raise VideoError(f"Couldn't download the video from Gemini (error {r.status_code}).")
    return r.content


# ---------------------------------------------------------------- 2. finishing with the agent's own layers

def duration(mp4: bytes) -> float:
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "in.mp4"
        path.write_bytes(mp4)
        out = subprocess.run([ffmpeg(), "-hide_banner", "-i", str(path)], capture_output=True, text=True).stderr
    m = re.search(r"Duration: (\d+):(\d+):(\d+(?:\.\d+)?)", out)
    if not m:
        raise VideoError("The video file from Gemini couldn't be read.")
    h, mnt, s = m.groups()
    return int(h) * 3600 + int(mnt) * 60 + float(s)


def has_audio(path: Path) -> bool:
    out = subprocess.run([ffmpeg(), "-hide_banner", "-i", str(path)], capture_output=True, text=True).stderr
    return "Audio:" in out


def compose(brand: Brand, clip: bytes, fields: dict) -> bytes:
    """Lay the hook, price, end card and logo over the clip → Instagram-ready MP4 (1080×1920, H.264, AAC)."""
    layers = renderer.render(brand, "reel_overlay.html",
                             [{**fields, "part": p} for p in ("hook", "price", "end", "logo")],
                             size=REEL_SIZE, transparent=True)
    seconds = duration(clip)
    hook_end = min(3.0, seconds * 0.35)
    end_start = max(hook_end + 1.0, seconds - 2.5)
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        (tmp / "clip.mp4").write_bytes(clip)
        for i, layer in enumerate(layers):
            (tmp / f"layer{i}.png").write_bytes(layer)
        audio = has_audio(tmp / "clip.mp4")
        graph = (
            "[0:v]scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,setsar=1,fps=30[base];"
            f"[base][1:v]overlay=0:0:enable='between(t,0,{hook_end:.2f})'[a];"
            f"[a][2:v]overlay=0:0:enable='between(t,{hook_end:.2f},{end_start:.2f})'[b];"
            f"[b][3:v]overlay=0:0:enable='gte(t,{end_start:.2f})'[c];"
            f"[c][4:v]overlay=0:0:enable='lt(t,{end_start:.2f})'[out]"
        )
        cmd = [ffmpeg(), "-hide_banner", "-loglevel", "error", "-y", "-i", str(tmp / "clip.mp4")]
        for i in range(4):
            cmd += ["-loop", "1", "-t", f"{seconds:.2f}", "-i", str(tmp / f"layer{i}.png")]
        if not audio:  # Instagram plays reels with sound; add quiet silence if the clip has none
            cmd += ["-f", "lavfi", "-t", f"{seconds:.2f}", "-i", "anullsrc=channel_layout=stereo:sample_rate=44100"]
        cmd += ["-filter_complex", graph, "-map", "[out]", "-map", "0:a" if audio else "5:a",
                "-c:v", "libx264", "-preset", ENCODE_PRESET, "-crf", "20", "-pix_fmt", "yuv420p", "-profile:v", "high",
                "-r", "30", "-c:a", "aac", "-b:a", "128k", "-ar", "44100", "-shortest", "-movflags", "+faststart",
                str(tmp / "reel.mp4")]
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            log.error("FFmpeg failed: %s", result.stderr[-500:])
            raise VideoError("Couldn't put the text and logo on the reel. The details are on the Activity page.")
        return (tmp / "reel.mp4").read_bytes()


# ---------------------------------------------------------------- 3. frames for checking and thumbnails

def frames(mp4: bytes, at: list[float] | None = None) -> list[bytes]:
    """JPEG stills at the given seconds (default: start, middle, near the end)."""
    seconds = duration(mp4)
    at = at or [min(1.0, seconds * 0.15), seconds * 0.5, max(0.0, seconds - 1.0)]
    out = []
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "in.mp4"
        path.write_bytes(mp4)
        for t in at:
            r = subprocess.run([ffmpeg(), "-hide_banner", "-loglevel", "error", "-ss", f"{t:.2f}", "-i", str(path),
                                "-frames:v", "1", "-q:v", "3", "-f", "image2pipe", "-vcodec", "mjpeg", "-"],
                               capture_output=True)
            if r.returncode == 0 and r.stdout:
                out.append(r.stdout)
    if not out:
        raise VideoError("Couldn't read frames from the reel.")
    return out
