"""The Control Center: the one place you run the agent from.

Opens at http://localhost:8000 on your laptop (or your app's web address on
Railway/Render). Pages:

- Home     — what's done, what to do next, and the action buttons
- Connect  — paste keys with step-by-step help; "Save & test" for each service
- Brand    — store, accounts, posting times, mix, colours, logo, voice, rules
- Activity — what the agent did each day (the log), in plain text

The first visit asks you to create a password.
"""

import hmac
import json
import logging
import os
import re
import secrets
import time
from datetime import timedelta
from functools import wraps

from flask import (Flask, abort, flash, jsonify, redirect, render_template, request, send_file, session,
                   url_for)
from werkzeug.security import check_password_hash, generate_password_hash

from app import (DATA_DIR, LOGS_DIR, assistant, brand_editor, claude, insights, jobs, planner, producer, publisher,
                 secrets_store, status, updater)
from app.brands import BrandConfigError, list_brands, load_brand
from app.key_tests import run_test
from app.keys import KEYS_BY_NAME, SERVICES, SERVICES_BY_ID
from app.db import get_state
from app.web import brand_forms

log = logging.getLogger(__name__)

MAX_FAILED_LOGINS = 5
LOCKOUT_SECONDS = 300
_failed_logins: list[float] = []

CURRENT_PHASE = 4  # raise as each phase ships; tiles at or below it become live buttons

# Buttons for work the agent will do. Each turns on when its phase is built.
ACTIONS = [
    ("sync", "Get products from Shopify", "Copies your products, prices and stock into the agent.", 1),
    ("plan", "Make this week's plan", "Claude plans the week and sends it to your Telegram.", 1),
    ("drafts", "Create drafts now", "Makes the next posts and sends them to Telegram for approval.", 2),
    ("image", "Make one AI image", "Pick a product; Gemini makes a lifestyle image of it.", 2),
    ("reel", "Make one reel", "Pick a product; Google's video AI makes a short vertical video with your price and "
     "logo added. Takes 2–6 minutes, about ₹70.", 2.5),
    ("queue", "See scheduled posts", "Everything approved and waiting to go out.", 3),
    ("pause", "Pause / resume posting", "Stops all posting until you resume. Drafts and plans keep working.", 3),
    ("stats", "Performance", "Reach, saves, shares per post, and what the agent learned for next week.", 4),
]


def create_app() -> Flask:
    app = Flask(__name__)
    app.secret_key = secrets_store.get("FLASK_SECRET_KEY")
    app.config.update(
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        # On Railway/Render the site is https; set COOKIE_SECURE=1 there.
        SESSION_COOKIE_SECURE=os.environ.get("COOKIE_SECURE") == "1",
        PERMANENT_SESSION_LIFETIME=timedelta(hours=12),
        MAX_CONTENT_LENGTH=6 * 1024 * 1024,  # logo uploads
    )

    # ---------- login & safety ----------

    def password_is_set() -> bool:
        return bool(secrets_store.get("SETTINGS_PASSWORD") or secrets_store.get("SETTINGS_PASSWORD_HASH"))

    def password_matches(attempt: str) -> bool:
        plain = secrets_store.get("SETTINGS_PASSWORD")  # optional: set on the host's env screen
        if plain:
            return hmac.compare_digest(plain.encode(), attempt.encode())
        hashed = secrets_store.get("SETTINGS_PASSWORD_HASH")
        return bool(hashed) and check_password_hash(hashed, attempt)

    def csrf_token() -> str:
        if "csrf" not in session:
            session["csrf"] = secrets.token_urlsafe(32)
        return session["csrf"]

    @app.before_request
    def check_csrf():
        if request.method == "POST":
            sent = request.form.get("csrf") or request.headers.get("X-CSRF-Token", "")
            if not sent or not hmac.compare_digest(sent, session.get("csrf", "")):
                abort(400, "Page expired. Go back, refresh and try again.")

    def login_required(view):
        @wraps(view)
        def wrapper(*args, **kwargs):
            if not password_is_set():
                return redirect(url_for("setup_password"))
            if not session.get("logged_in"):
                return redirect(url_for("login"))
            return view(*args, **kwargs)
        return wrapper

    def current_brand_slug() -> str | None:
        brands = list_brands()
        slug = session.get("brand")
        return slug if slug in brands else (brands[0] if brands else None)

    @app.context_processor
    def template_globals():
        return {"csrf_token": csrf_token, "brands": list_brands(), "brand_slug": current_brand_slug(),
                "page": request.endpoint}

    @app.route("/setup-password", methods=["GET", "POST"])
    def setup_password():
        if password_is_set():
            return redirect(url_for("login"))
        if request.method == "POST":
            pw = request.form.get("password", "")
            if len(pw) < 8:
                flash("Use at least 8 characters.", "error")
            elif pw != request.form.get("confirm", ""):
                flash("The two passwords don't match.", "error")
            else:
                secrets_store.save("SETTINGS_PASSWORD_HASH", generate_password_hash(pw))
                session.clear()
                session["logged_in"] = True
                session.permanent = True
                log.info("Control Center password created")
                return redirect(url_for("home"))
        return render_template("setup_password.html")

    @app.route("/login", methods=["GET", "POST"])
    def login():
        if not password_is_set():
            return redirect(url_for("setup_password"))
        if request.method == "POST":
            now = time.time()
            _failed_logins[:] = [t for t in _failed_logins if now - t < LOCKOUT_SECONDS]
            if len(_failed_logins) >= MAX_FAILED_LOGINS:
                flash("Too many wrong tries. Wait 5 minutes and try again.", "error")
            elif password_matches(request.form.get("password", "")):
                session.clear()
                session["logged_in"] = True
                session.permanent = True
                log.info("Control Center: logged in")
                return redirect(url_for("home"))
            else:
                _failed_logins.append(now)
                log.warning("Control Center: wrong password")
                flash("Wrong password.", "error")
        return render_template("login.html")

    @app.route("/logout", methods=["POST"])
    def logout():
        session.clear()
        return redirect(url_for("login"))

    @app.route("/brand/switch", methods=["POST"])
    @login_required
    def switch_brand():
        slug = request.form.get("brand")
        if slug in list_brands():
            session["brand"] = slug
        return redirect(request.referrer or url_for("home"))

    # ---------- Home ----------

    @app.route("/")
    @login_required
    def home():
        slug = current_brand_slug()
        brand, brand_error = _load(slug)
        results = status.test_results(slug) if slug else {}
        connected = [s for s in SERVICES if results.get(s.id, {}).get("ok")]
        todo = _brand_todo(brand) if brand else []
        next_service = next((s for s in SERVICES if not results.get(s.id, {}).get("ok")), None)
        job_status = jobs.status(slug) if slug else {}
        last_sync = json.loads(get_state(slug, "last_sync") or "null") if slug else None
        plan_rows = planner.upcoming_plan(brand) if brand else []
        return render_template("home.html", brand=brand, brand_error=brand_error, services=SERVICES,
                               results=results, connected=connected, brand_todo=todo,
                               next_service=next_service, actions=ACTIONS, current_phase=CURRENT_PHASE,
                               jobs=job_status, any_running=any(j["state"] == "running" for j in job_status.values()),
                               last_sync=last_sync, plan_count=len(plan_rows),
                               styles=planner.catalog_styles(brand) if brand else [],
                               ready=set(jobs.ACTIONS) | {"queue", "pause"},  # tiles that have something behind them
                               reels_made=producer.reels_this_week(brand) if brand else 0,
                               reel_cap=producer.reel_cap(brand) if brand else 0,
                               paused=publisher.is_paused(brand) if brand else False,
                               queued=sum(len(g["posts"]) for g in publisher.queue(brand)) if brand else 0,
                               version=updater.installed(), has_github=bool(secrets_store.get("GITHUB_TOKEN")),
                               next_plan=jobs.next_plan_time(slug) if slug else None)

    @app.route("/action/<action>", methods=["POST"])
    @login_required
    def run_action(action):
        if action not in jobs.ACTIONS:
            abort(404)
        slug = current_brand_slug()
        kwargs = {}
        if action in ("image", "reel"):
            kwargs = {"style_id": request.form.get("style_id", ""), "note": request.form.get("note", "")[:300]}
        elif action == "regen":
            kwargs = {"draft_id": int(request.form.get("draft_id", "0") or 0)}
        if jobs.start(slug, action, **kwargs):
            flash("Started — this page updates by itself when it's done.", "ok")
        else:
            flash("That's already running. Give it a moment.", "warn")
        back = request.form.get("back") or ""
        return redirect(back if back.startswith("/") and not back.startswith("//") else url_for("home"))

    @app.route("/updates/check", methods=["POST"])
    @login_required
    def check_updates():
        try:
            info = updater.check()
            if info["available"]:
                flash(f"A new version is ready ({info['latest']['date']}: {info['latest']['message']}). "
                      "Press Update agent.", "ok")
            else:
                flash("You have the newest version.", "ok")
        except updater.UpdateError as e:
            flash(str(e), "error")
        return redirect(url_for("home") + "#updates")

    @app.route("/job-status")
    @login_required
    def job_status():
        return jsonify(jobs.status(current_brand_slug()))

    # ---------- Plan & Products ----------

    @app.route("/plan")
    @login_required
    def plan_page():
        slug = current_brand_slug()
        brand, brand_error = _load(slug)
        job_status = jobs.status(slug) if slug else {}
        return render_template("plan.html", brand=brand, brand_error=brand_error,
                               rows=planner.upcoming_plan(brand) if brand else [], price_text=planner.price_text,
                               jobs=job_status, any_running=any(j["state"] == "running" for j in job_status.values()),
                               next_plan=jobs.next_plan_time(slug) if slug else None)

    @app.route("/products")
    @login_required
    def products_page():
        slug = current_brand_slug()
        brand, brand_error = _load(slug)
        job_status = jobs.status(slug) if slug else {}
        return render_template("products.html", brand=brand, brand_error=brand_error,
                               styles=planner.catalog_styles(brand) if brand else [],
                               paused=publisher.is_paused(brand) if brand else False,
                               queued=sum(len(g["posts"]) for g in publisher.queue(brand)) if brand else 0,
                               last_sync=json.loads(get_state(slug, "last_sync") or "null") if slug else None,
                               jobs=job_status, any_running=any(j["state"] == "running" for j in job_status.values()))

    @app.route("/drafts")
    @login_required
    def drafts_page():
        slug = current_brand_slug()
        brand, brand_error = _load(slug)
        job_status = jobs.status(slug) if slug else {}
        return render_template("drafts.html", brand=brand, brand_error=brand_error,
                               drafts=producer.recent_drafts(brand) if brand else [], price_text=planner.price_text,
                               jobs=job_status, any_running=any(j["state"] == "running" for j in job_status.values()))

    @app.route("/drafts/<int:draft_id>/<decision>", methods=["POST"])
    @login_required
    def draft_decision(draft_id, decision):
        slug = current_brand_slug()
        brand, error = _load(slug)
        d = producer.get_draft(brand, draft_id) if brand else None
        if not d:
            abort(404)
        if decision in ("approve", "reject") and d["status"] == "pending":
            when = producer.decide(brand, draft_id, decision)
            flash(f"Draft #{draft_id} approved — it will be posted on {when:%a %d %b at %I:%M %p}." if when
                  else f"Draft #{draft_id} rejected.", "ok")
        elif decision == "regenerate" and d["status"] in ("pending", "rejected"):
            note = request.form.get("note", "").strip()[:500]
            if jobs.start(slug, "regen", draft_id=draft_id, note=note):
                producer.decide(brand, draft_id, "regenerate", note or None)
                flash(f"Making a new version of draft #{draft_id} — it will also arrive in Telegram.", "ok")
            else:
                flash("Already remaking a draft — try again in a minute.", "warn")
        elif decision == "edit_caption":
            caption = request.form.get("caption", "").strip()
            if caption:
                producer.decide(brand, draft_id, "edit_caption", caption)
                flash(f"Caption of draft #{draft_id} saved.", "ok")
        else:
            flash(f"Draft #{draft_id} is already {d['status']}.", "warn")
        return redirect(url_for("drafts_page") + f"#d{draft_id}")

    @app.route("/performance")
    @login_required
    def performance_page():
        slug = current_brand_slug()
        brand, brand_error = _load(slug)
        days = 28 if request.args.get("days") == "28" else 7
        job_status = jobs.status(slug) if slug else {}
        return render_template("performance.html", brand=brand, brand_error=brand_error, days=days,
                               rep=insights.report(brand, days) if brand else None,
                               lessons=insights.lessons(brand) if brand else None,
                               jobs=job_status, any_running=any(j["state"] == "running" for j in job_status.values()))

    @app.route("/queue")
    @login_required
    def queue_page():
        slug = current_brand_slug()
        brand, brand_error = _load(slug)
        groups = publisher.queue(brand, include_done=True) if brand else []
        upcoming = [g for g in groups if any(p["status"] != "published" for p in g["posts"])]
        done = [g for g in groups if all(p["status"] == "published" for p in g["posts"])][:20]
        upcoming.sort(key=lambda g: g["when"])
        drafts = {g["draft_id"]: producer.get_draft(brand, g["draft_id"]) for g in groups} if brand else {}
        return render_template("queue.html", brand=brand, brand_error=brand_error, upcoming=upcoming, done=done,
                               drafts=drafts, names=publisher.PLATFORM_NAMES,
                               paused=publisher.is_paused(brand) if brand else False)

    @app.route("/queue/<int:post_id>/<what>", methods=["POST"])
    @login_required
    def queue_action(post_id, what):
        brand, _ = _load(current_brand_slug())
        if not brand or what not in ("now", "cancel"):
            abort(404)
        if what == "now":
            publisher.retry_now(brand, post_id)
            flash("It will be posted within a minute" + (" — but posting is paused; press Resume first." if
                  publisher.is_paused(brand) else "."), "ok")
        else:
            publisher.cancel(brand, post_id)
            flash("Cancelled — that post won't go out.", "ok")
        return redirect(url_for("queue_page"))

    @app.route("/chat", methods=["GET", "POST"])
    @login_required
    def chat_page():
        brand, brand_error = _load(current_brand_slug())
        if request.method == "POST" and brand:
            text = (request.form.get("message") or "").strip()[:2000]
            if text:
                try:
                    assistant.reply(brand, text)
                except claude.ClaudeError as e:
                    flash(str(e), "error")
            return redirect(url_for("chat_page"))
        return render_template("chat.html", brand=brand, brand_error=brand_error,
                               turns=assistant.history(brand) if brand else [],
                               questions=assistant.open_confirmations(brand) if brand else [])

    @app.route("/chat/confirm/<token>/<answer>", methods=["POST"])
    @login_required
    def chat_confirm(token, answer):
        brand, _ = _load(current_brand_slug())
        if not brand or answer not in ("yes", "no"):
            abort(404)
        flash(assistant.confirm(brand, token, answer == "yes"), "ok")
        return redirect(url_for("chat_page"))

    @app.route("/media/<int:draft_id>/<int:n>")
    @login_required
    def draft_media(draft_id, n):
        brand, _ = _load(current_brand_slug())
        d = producer.get_draft(brand, draft_id) if brand else None
        if not d or not 1 <= n <= len(d["files"]) or not d["files"][n - 1].exists():
            abort(404)
        path = d["files"][n - 1].resolve()
        if DATA_DIR.resolve() not in path.parents:
            abort(404)
        return send_file(path, mimetype="video/mp4" if path.suffix == ".mp4" else "image/jpeg", max_age=0,
                         conditional=True)  # conditional=True lets the browser stream/seek the video

    @app.route("/check-all", methods=["POST"])
    @login_required
    def check_all():
        slug = current_brand_slug()
        brand, error = _load(slug)
        if error:
            flash(error, "error")
            return redirect(url_for("home"))
        for service in SERVICES:
            ok, message = run_test(service.id, brand)
            status.save_test_result(slug, service.id, ok, message)
            brand, _ = _load(slug)  # a test may have filled in IDs
        flash("Checked every connection. Results are below.", "ok")
        return redirect(url_for("home"))

    # ---------- Connect ----------

    @app.route("/connect")
    @login_required
    def connect():
        slug = current_brand_slug()
        brand, brand_error = _load(slug)
        prefix = _prefix(brand)
        results = status.test_results(slug) if slug else {}
        cards = []
        for s in SERVICES:
            fields = []
            for name in s.keys:
                spec = KEYS_BY_NAME[name]
                value = secrets_store.get(_env_name(name, prefix))
                fields.append({"spec": spec, "is_set": bool(value),
                               "display": (secrets_store.mask(value) if spec.secret else value) if value else ""})
            cards.append({"service": s, "fields": fields, "result": results.get(s.id)})
        store_domain = brand.config["shopify"].get("store_domain", "") if brand else ""
        if "your-store" in (store_domain or ""):
            store_domain = ""
        return render_template("connect.html", cards=cards, brand=brand, brand_error=brand_error,
                               store_domain=store_domain,
                               on_host=bool(os.environ.get("RAILWAY_ENVIRONMENT") or os.environ.get("RENDER")))

    @app.route("/connect/<service_id>", methods=["POST"])
    @login_required
    def connect_save(service_id):
        service = SERVICES_BY_ID.get(service_id) or abort(404)
        slug = current_brand_slug()
        brand, error = _load(slug)
        if error:
            flash(error, "error")
            return redirect(url_for("connect"))
        prefix = _prefix(brand)
        try:
            if service.id == "shopify" and request.form.get("store_domain", "").strip():
                brand_editor.update_config(slug, {
                    "shopify.store_domain": brand_editor.normalize_shop_domain(request.form["store_domain"])})
            for name in service.keys:
                value = request.form.get(name, "").strip()
                if request.form.get(f"clear_{name}"):
                    secrets_store.save(_env_name(name, prefix), "")
                    log.info("Control Center: %s cleared", _env_name(name, prefix))
                elif value:
                    secrets_store.save(_env_name(name, prefix), value)
                    log.info("Control Center: %s updated", _env_name(name, prefix))  # never the value
        except (ValueError, BrandConfigError) as e:
            flash(str(e), "error")
            return redirect(url_for("connect") + f"#{service.id}")

        brand, _ = _load(slug)
        ok, message = run_test(service.id, brand)
        status.save_test_result(slug, service.id, ok, message)
        flash(("✅ " if ok else "❌ ") + f"{service.title}: {message}", "ok" if ok else "error")
        return redirect(url_for("connect") + f"#{service.id}")

    # Kept for the old Settings page link.
    @app.route("/settings")
    def settings():
        return redirect(url_for("connect"))

    @app.route("/settings/test", methods=["POST"])
    @login_required
    def test_key():
        slug = current_brand_slug()
        brand, error = _load(slug)
        if error:
            return jsonify(ok=False, message=error)
        test_name = request.form.get("test", "")
        ok, message = run_test(test_name, brand)
        if test_name in SERVICES_BY_ID:
            status.save_test_result(slug, test_name, ok, message)
        return jsonify(ok=ok, message=message)

    # ---------- Brand ----------

    @app.route("/brand")
    @login_required
    def brand_page():
        slug = current_brand_slug()
        brand, brand_error = _load(slug)
        if brand is None:
            return render_template("brand.html", brand=None, brand_error=brand_error)
        slots = {s["day"]: s["time"] for s in reversed(brand.config["schedule"].get("slots", []))}
        return render_template(
            "brand.html", brand=brand, brand_error=None, c=brand.config, slots=slots, f=brand_forms,
            has_logo=bool(brand_editor.logo_file(slug)), has_logo_white=bool(brand_editor.logo_file(slug, True)),
            store_domain="" if "your-store" in brand.config["shopify"].get("store_domain", "")
            else brand.config["shopify"].get("store_domain", ""))

    @app.route("/brand/<section>", methods=["POST"])
    @login_required
    def brand_save(section):
        slug = current_brand_slug()
        if section == "rules":
            try:
                brand_editor.save_rules(slug, request.form.get("rules", ""))
                flash("Brand rules saved.", "ok")
                log.info("Control Center: brand rules updated")
            except BrandConfigError as e:
                flash(str(e), "error")
            return redirect(url_for("brand_page") + "#rules")
        if section == "logo":
            upload = request.files.get("logo")
            if not upload or not upload.filename:
                flash("Choose a logo file first.", "error")
            else:
                try:
                    brand_editor.save_logo(slug, upload.read(), white_version=request.form.get("which") == "white")
                    flash("Logo saved.", "ok")
                    log.info("Control Center: logo uploaded")
                except BrandConfigError as e:
                    flash(str(e), "error")
            return redirect(url_for("brand_page") + "#look")
        parse = brand_forms.SECTIONS.get(section) or abort(404)
        try:
            brand_editor.update_config(slug, parse(request.form))
            flash("Saved.", "ok")
            log.info("Control Center: brand settings '%s' updated", section)
            if section == "schedule":
                jobs.reload_schedule()
        except BrandConfigError as e:
            flash(str(e), "error")
        return redirect(url_for("brand_page") + f"#{section}")

    @app.route("/brand/logo/<which>")
    @login_required
    def brand_logo(which):
        path = brand_editor.logo_file(current_brand_slug(), white_version=(which == "white"))
        if not path:
            abort(404)
        return send_file(path, max_age=0)

    # ---------- Activity ----------

    @app.route("/activity")
    @login_required
    def activity():
        files = sorted((p.name for p in LOGS_DIR.glob("*.log") if re.fullmatch(r"\d{4}-\d{2}-\d{2}\.log", p.name)),
                       reverse=True) if LOGS_DIR.exists() else []
        chosen = request.args.get("day") or (files[0] if files else None)
        lines = []
        if chosen in files:
            text = (LOGS_DIR / chosen).read_text(encoding="utf-8", errors="replace")
            lines = list(reversed(text.splitlines()[-500:]))  # newest first
        return render_template("activity.html", files=files, chosen=chosen, lines=lines)

    return app


# ---------- helpers ----------

def _load(slug):
    if not slug:
        return None, "No brand folder found in brands/."
    try:
        return load_brand(slug), None
    except BrandConfigError as e:
        return None, str(e)


def _prefix(brand) -> str:
    return (brand.config.get("env_prefix", "") or "") if brand else ""


def _env_name(name: str, prefix: str) -> str:
    # Claude and Gemini keys are shared across brands; everything else is per brand.
    if name in ("ANTHROPIC_API_KEY", "GEMINI_API_KEY"):
        return name
    return prefix + name


def _brand_todo(brand) -> list[tuple[str, str]]:
    """Brand details still missing, as (what, which Brand-page section)."""
    c = brand.config
    todo = []
    if "your-store" in (c["shopify"].get("store_domain") or ""):
        todo.append(("Add your Shopify store address", "basics"))
    if not brand_editor.logo_file(brand.slug):
        todo.append(("Upload your logo", "look"))
    if c["style"]["colors"].get("primary", "").upper() == "#0B2545":
        todo.append(("Set your brand colours (still the starter colours)", "look"))
    return todo
