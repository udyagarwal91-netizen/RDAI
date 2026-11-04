"""Agent HQ pages added on top of the Control Center: Goals, phone access, voice in the app,
and the files that let a phone "install" the app (manifest, icon, service worker).

Routes are added by register(), called from create_app(), so they share its login check.
"""

import io
import json
import logging
import os
import socket
import threading

from flask import Response, abort, flash, jsonify, redirect, render_template, request, send_file, url_for

from app import goals, secrets_store

log = logging.getLogger(__name__)


def lan_address(port: int) -> str | None:
    """This computer's address on the home Wi-Fi, e.g. http://192.168.1.20:8000 (nothing is sent anywhere)."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("10.255.255.255", 1))
            ip = s.getsockname()[0]
    except OSError:
        return None
    return None if ip.startswith("127.") else f"http://{ip}:{port}"


def on_host() -> bool:
    """True on Railway/Render (they set PORT)."""
    return "PORT" in os.environ


def register(app, login_required, current_slug, load):
    # ---------- Goals ----------

    @app.route("/goals", methods=["GET", "POST"])
    @login_required
    def goals_page():
        brand, brand_error = load(current_slug())
        if request.method == "POST" and brand:
            try:
                goals.update_from_form(brand, request.form)
                flash("Goals saved. Every agent uses the new numbers from now on.", "ok")
                log.info("Control Center: goals updated")
            except ValueError as e:
                flash(str(e), "error")
            return redirect(url_for("goals_page"))
        return render_template("goals.html", brand=brand, brand_error=brand_error,
                               doc=goals.load(brand) if brand else None,
                               progress=goals.progress(brand) if brand else [],
                               day=goals.day_number(goals.load(brand)) if brand else 0,
                               guard_labels=goals.GUARDRAILS, biz_labels=goals.BUSINESS,
                               milestones=goals.MILESTONES)

    # ---------- Phone access on home Wi-Fi ----------

    @app.route("/phone", methods=["POST"])
    @login_required
    def phone_access():
        if on_host():
            abort(404)
        turn_on = request.form.get("on") == "1"
        secrets_store.save("LAN_ACCESS", "1" if turn_on else "0")
        log.info("Control Center: phone access on Wi-Fi %s — restarting", "on" if turn_on else "off")
        flash(("Phone access switched on" if turn_on else "Phone access switched off") +
              ". The agent restarts in a few seconds — refresh this page in 15 seconds.", "ok")
        threading.Timer(2.0, lambda: os._exit(3)).start()  # the start script restarts the agent on exit code 3
        return redirect(url_for("home"))

    # ---------- Voice in the app ----------

    @app.route("/chat/voice", methods=["POST"])
    @login_required
    def chat_voice():
        from app import assistant, claude, voice
        brand, error = load(current_slug())
        audio = request.files.get("audio")
        if not brand or not audio:
            return jsonify({"error": error or "No recording received."}), 400
        name = (audio.filename or "").lower()
        suffix = next((s for s in (".mp4", ".m4a", ".ogg", ".mp3", ".wav", ".aac", ".3gp", ".amr")
                       if name.endswith(s)), ".webm")
        try:
            heard = voice.transcribe(brand, voice.to_mp3(audio.read(), suffix), "audio/mp3")
            answer = assistant.reply(brand, heard)
        except (voice.VoiceError, claude.ClaudeError) as e:
            return jsonify({"error": str(e)}), 200
        return jsonify({"heard": heard, "reply": answer["text"]})

    # ---------- Installable app (phone home screen) ----------

    @app.route("/manifest.webmanifest")
    def manifest():
        body = {"name": "Agent HQ", "short_name": "Agent HQ", "start_url": "/", "scope": "/",
                "display": "standalone", "background_color": "#f6f5f1", "theme_color": "#0B2545",
                "icons": [{"src": url_for("app_icon", size=s), "sizes": f"{s}x{s}", "type": "image/png",
                           "purpose": "any maskable"} for s in (192, 512)]}
        return Response(json.dumps(body), mimetype="application/manifest+json")

    @app.route("/app-icon-<int:size>.png")
    def app_icon(size):
        if size not in (180, 192, 512):
            abort(404)
        from PIL import Image, ImageDraw, ImageFont
        img = Image.new("RGB", (size, size), "#0B2545")
        draw = ImageDraw.Draw(img)
        try:
            font = ImageFont.load_default(size=int(size * 0.42))
        except TypeError:
            font = ImageFont.load_default()
        draw.text((size / 2, size / 2), "HQ", fill="white", font=font, anchor="mm")
        buf = io.BytesIO()
        img.save(buf, "PNG")
        buf.seek(0)
        return send_file(buf, mimetype="image/png", max_age=86400)

    @app.route("/sw.js")
    def service_worker():
        # Minimal worker: lets phones install the app; pages always come fresh from the agent.
        js = "self.addEventListener('install', e => self.skipWaiting());\n" \
             "self.addEventListener('activate', e => self.clients.claim());\n" \
             "self.addEventListener('fetch', () => {});\n"
        return Response(js, mimetype="text/javascript", headers={"Cache-Control": "no-cache"})
