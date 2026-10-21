"""The agent's actions, and running them in the background.

Buttons in the Control Center and the weekly timer both call these, so a
plan made by the timer and a plan made by pressing the button are identical.
Slow work runs in the background; its progress is stored so the Home page
can show "Working…" and then the result.
"""

import json
import logging
import threading
from datetime import datetime, timedelta, timezone

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from app import planner, shopify, telegram
from app.brands import BrandConfigError, list_brands, load_brand
from app.db import get_state, set_state

log = logging.getLogger(__name__)
_running: set[tuple[str, str]] = set()
_lock = threading.Lock()


# ---------------------------------------------------------------- actions

def sync(slug: str) -> str:
    brand = load_brand(slug)
    r = shopify.sync_products(brand)
    msg = (f"Got {r.total} products from Shopify ({r.in_stock} active and in stock"
           + (f", {r.new} new" if r.new else "") + (f", {r.removed} removed" if r.removed else "") + ").")
    if r.problems:
        msg += f" ⚠️ {r.problems} product(s) have a price mistake in Shopify — see the Products page."
    return msg


def plan(slug: str) -> str:
    brand = load_brand(slug)
    last = json.loads(get_state(slug, "last_sync") or "{}")
    if not last or datetime.fromisoformat(last["at"]) < datetime.now(timezone.utc) - timedelta(hours=6):
        sync(slug)  # plan with fresh prices and stock
    result = planner.make_plan(brand)
    rows = planner.upcoming_plan(brand)
    try:
        telegram.send_message(brand, telegram.format_plan(brand, result["summary"], rows, result["cost_usd"]))
        sent = " and sent it to your Telegram"
    except telegram.TelegramError as e:
        sent = f" (couldn't send to Telegram: {e})"
    warnings = sum(len(r["warnings"]) for r in rows)
    note = f" {warnings} item(s) are marked ⚠️ for you to check." if warnings else ""
    return f"Planned {len(result['rows'])} posts{sent}. Cost about US${result['cost_usd']:.2f}.{note}"


def send_plan(slug: str) -> str:
    brand = load_brand(slug)
    telegram.send_message(brand, telegram.format_plan(brand, None, planner.upcoming_plan(brand)))
    return "Sent the plan to your Telegram."


ACTIONS = {"sync": sync, "plan": plan, "send_plan": send_plan}
FRIENDLY_ERRORS = (shopify.ShopifyError, planner.PlanError, telegram.TelegramError, BrandConfigError)


# ---------------------------------------------------------------- background running

def start(slug: str, action: str) -> bool:
    """Start an action in the background. False if it's already running."""
    with _lock:
        if (slug, action) in _running:
            return False
        _running.add((slug, action))
    _set(slug, action, "running", "Working…")
    threading.Thread(target=_run, args=(slug, action), name=f"job-{action}", daemon=True).start()
    return True


def _run(slug: str, action: str) -> None:
    try:
        log.info("Started: %s (%s)", action, slug)
        message = ACTIONS[action](slug)
        _set(slug, action, "done", message)
        log.info("Finished: %s — %s", action, message)
    except FRIENDLY_ERRORS as e:
        _set(slug, action, "failed", str(e))
        log.warning("Failed: %s — %s", action, e)
    except Exception as e:
        _set(slug, action, "failed", "Something unexpected went wrong. The details are on the Activity page.")
        log.exception("Failed: %s — %s", action, e)
    finally:
        with _lock:
            _running.discard((slug, action))


def _set(slug: str, action: str, state: str, message: str) -> None:
    set_state(slug, f"job:{action}", json.dumps({
        "state": state, "message": message, "at": datetime.now().strftime("%d %b, %I:%M %p")}))


def status(slug: str) -> dict[str, dict]:
    out = {}
    for action in ACTIONS:
        raw = get_state(slug, f"job:{action}")
        if raw:
            out[action] = json.loads(raw)
            if out[action]["state"] == "running" and (slug, action) not in _running:
                out[action]["state"] = "failed"  # the app was closed while it was running
                out[action]["message"] = "Stopped because the app was closed. Press the button again."
    return out


# ---------------------------------------------------------------- the timer

_scheduler: BackgroundScheduler | None = None


def start_scheduler() -> None:
    global _scheduler
    _scheduler = BackgroundScheduler(timezone="Asia/Kolkata", job_defaults={"misfire_grace_time": 3600,
                                                                            "coalesce": True})
    _scheduler.start()
    reload_schedule()


def reload_schedule() -> None:
    """(Re)create the timed jobs from each brand's settings. Called at start and after Brand changes."""
    if _scheduler is None:
        return
    _scheduler.remove_all_jobs()
    for slug in list_brands():
        try:
            sched = load_brand(slug).config["schedule"]
        except BrandConfigError as e:
            log.error("Timer not set for %s: %s", slug, e)
            continue
        tz = sched.get("timezone", "Asia/Kolkata")
        hh, mm = map(int, str(sched.get("plan_time", "10:00")).split(":"))
        _scheduler.add_job(start, CronTrigger(day_of_week=sched.get("plan_day", "sun"), hour=hh, minute=mm,
                                              timezone=tz), args=[slug, "plan"], id=f"{slug}:plan")
        _scheduler.add_job(start, CronTrigger(hour=6, minute=15, timezone=tz), args=[slug, "sync"],
                           id=f"{slug}:sync")
        log.info("Timer set for %s: weekly plan every %s at %s, product sync daily at 06:15",
                 slug, sched.get("plan_day"), sched.get("plan_time"))


def next_plan_time(slug: str) -> str | None:
    job = _scheduler.get_job(f"{slug}:plan") if _scheduler else None
    return job.next_run_time.strftime("%a %d %b, %I:%M %p") if job and job.next_run_time else None
