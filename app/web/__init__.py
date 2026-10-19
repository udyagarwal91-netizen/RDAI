"""The browser Settings page: log in, paste keys, press Test.

Runs at http://localhost:8000 on your laptop. On Railway/Render it runs at
your app's web address. The first visit asks you to create a password.
"""

import hmac
import logging
import os
import secrets
import time
from datetime import timedelta
from functools import wraps

from flask import Flask, abort, flash, jsonify, redirect, render_template, request, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash

from app import secrets_store
from app.brands import BrandConfigError, list_brands, load_brand
from app.key_tests import run_test
from app.keys import KEYS, KEYS_BY_NAME

log = logging.getLogger(__name__)

MAX_FAILED_LOGINS = 5
LOCKOUT_SECONDS = 300
_failed_logins: list[float] = []


def create_app() -> Flask:
    app = Flask(__name__)
    app.secret_key = secrets_store.get("FLASK_SECRET_KEY")
    app.config.update(
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        # On Railway/Render the site is https; set COOKIE_SECURE=1 there.
        SESSION_COOKIE_SECURE=os.environ.get("COOKIE_SECURE") == "1",
        PERMANENT_SESSION_LIFETIME=timedelta(hours=12),
    )

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

    app.jinja_env.globals["csrf_token"] = csrf_token

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

    def current_brand():
        brands = list_brands()
        slug = session.get("brand")
        if slug not in brands:
            slug = brands[0] if brands else None
        return slug, brands

    @app.route("/")
    def home():
        return redirect(url_for("settings"))

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
                log.info("Settings page password created")
                return redirect(url_for("settings"))
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
                log.info("Settings page: logged in")
                return redirect(url_for("settings"))
            else:
                _failed_logins.append(now)
                log.warning("Settings page: wrong password")
                flash("Wrong password.", "error")
        return render_template("login.html")

    @app.route("/logout", methods=["POST"])
    def logout():
        session.clear()
        return redirect(url_for("login"))

    @app.route("/settings")
    @login_required
    def settings():
        slug, brands = current_brand()
        brand_error, prefix = None, ""
        if slug:
            try:
                prefix = load_brand(slug).config.get("env_prefix", "") or ""
            except BrandConfigError as e:
                brand_error = str(e)
        rows = []
        for k in KEYS:
            env_name = _env_name(k.name, prefix)
            value = secrets_store.get(env_name)
            rows.append({
                "spec": k,
                "env_name": env_name,
                "is_set": bool(value),
                "display": (value if not k.secret else secrets_store.mask(value)) if value else "not set",
            })
        groups = {}
        for row in rows:
            groups.setdefault(row["spec"].test or "other", []).append(row)
        return render_template("settings.html", groups=groups, brands=brands, brand=slug,
                               brand_error=brand_error, on_host=bool(os.environ.get("RAILWAY_ENVIRONMENT")
                                                                     or os.environ.get("RENDER")))

    @app.route("/settings/brand", methods=["POST"])
    @login_required
    def switch_brand():
        slug = request.form.get("brand")
        if slug in list_brands():
            session["brand"] = slug
        return redirect(url_for("settings"))

    @app.route("/settings/save", methods=["POST"])
    @login_required
    def save_key():
        name = request.form.get("name", "")
        value = request.form.get("value", "")
        slug, _ = current_brand()
        spec = KEYS_BY_NAME.get(name)
        if spec is None:
            abort(400)
        prefix = _brand_prefix(slug)
        try:
            if request.form.get("action") == "clear":
                secrets_store.save(_env_name(name, prefix), "")
                flash(f"{spec.label} removed.", "ok")
                log.info("Settings page: %s cleared", _env_name(name, prefix))
            elif not value.strip():
                flash(f"Paste the {spec.label} in the box first.", "error")
            else:
                secrets_store.save(_env_name(name, prefix), value)
                flash(f"{spec.label} saved. Press Test to check it works.", "ok")
                log.info("Settings page: %s updated", _env_name(name, prefix))  # never the value
        except ValueError as e:
            flash(str(e), "error")
        return redirect(url_for("settings") + f"#{spec.test or ''}")

    @app.route("/settings/test", methods=["POST"])
    @login_required
    def test_key():
        slug, _ = current_brand()
        if not slug:
            return jsonify(ok=False, message="No brand folder found in brands/.")
        try:
            brand = load_brand(slug)
        except BrandConfigError as e:
            return jsonify(ok=False, message=str(e))
        ok, message = run_test(request.form.get("test", ""), brand)
        return jsonify(ok=ok, message=message)

    return app


def _brand_prefix(slug: str | None) -> str:
    if not slug:
        return ""
    try:
        return load_brand(slug).config.get("env_prefix", "") or ""
    except BrandConfigError:
        return ""


def _env_name(name: str, prefix: str) -> str:
    # Claude and Gemini keys are shared across brands; everything else is per brand.
    if name in ("ANTHROPIC_API_KEY", "GEMINI_API_KEY"):
        return name
    return prefix + name
