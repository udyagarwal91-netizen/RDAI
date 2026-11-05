"""The agent's actions, and running them in the background.

Buttons in the Control Center and the weekly timer both call these, so a
plan made by the timer and a plan made by pressing the button are identical.
Slow work runs in the background; its progress is stored so the Home page
can show "Working…" and then the result.
"""

import json
import logging
import os
import threading
from datetime import datetime, timedelta, timezone

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from app import (claude, gemini, insights, planner, producer, publisher, renderer, shopify, telegram, updater,
                 video)
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


def prepare_for_planning(slug: str) -> None:
    """Fresh prices/stock from Shopify and fresh post results from Meta before any plan."""
    brand = load_brand(slug)
    last = json.loads(get_state(slug, "last_sync") or "{}")
    if not last or datetime.fromisoformat(last["at"]) < datetime.now(timezone.utc) - timedelta(hours=6):
        sync(slug)  # plan with fresh prices and stock
    if brand.secret("META_ACCESS_TOKEN"):
        try:  # plan with fresh results
            insights.collect(brand)
            insights.make_lessons(brand)
        except (publisher.PublishError, claude.ClaudeError) as e:
            log.warning("Couldn't refresh results before planning: %s", e)


def plan(slug: str, note: str = "") -> str:
    brand = load_brand(slug)
    prepare_for_planning(slug)
    result = planner.make_plan(brand, note=note)
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


def update(slug: str) -> str:
    if "PORT" in os.environ:  # online hosting rebuilds from GitHub by itself
        raise updater.UpdateError("Online, the agent updates itself when a new version is published — "
                                  "no button needed.")
    busy = [a for s, a in _running if a != "update"]
    if busy:
        raise updater.UpdateError("The agent is busy right now. Try again in a minute, when it has finished.")
    message = updater.apply_update()
    # Give the page a moment to show the result, then stop. The start file starts the agent again.
    threading.Timer(3, lambda: os._exit(updater.RESTART_EXIT_CODE)).start()
    return message


FRIENDLY_ERRORS = (shopify.ShopifyError, planner.PlanError, telegram.TelegramError, BrandConfigError,
                   updater.UpdateError, producer.DraftError, gemini.GeminiError, claude.ClaudeError,
                   renderer.RenderError, video.VideoError, publisher.PublishError)


def drafts(slug: str, days: int = 7) -> str:
    """Make drafts for planned posts in the next `days` days and send each to Telegram."""
    brand = load_brand(slug)
    rows = producer.due_plan_rows(brand, days=days)
    if not rows:
        return f"Nothing to draft — no planned posts in the next {days} days without a draft. Make a plan first."
    made, problems, cost, unsent = 0, [], 0.0, 0
    for row in rows:
        try:
            draft_id = producer.make_draft(brand, row)
            d = producer.get_draft(brand, draft_id)
            cost += d["cost_usd"]
            made += 1
            try:
                telegram.send_draft(brand, d)
            except telegram.TelegramError as e:
                unsent += 1
                log.warning("Draft #%d made but not sent to Telegram: %s", draft_id, e)
        except FRIENDLY_ERRORS as e:
            when = datetime.fromisoformat(row["scheduled_for"]).strftime("%a %d %b")
            problems.append(f"{when}: {e}")
            log.warning("Draft for %s failed: %s", row["scheduled_for"], e)
    msg = f"Made {made} draft(s)" + (" and sent them to Telegram" if not unsent else
                                      f" ({unsent} couldn't be sent to Telegram — see the Drafts page)") + \
          f". Cost about US${cost:.2f}."
    if problems:
        msg += f" {len(problems)} couldn't be made — " + " | ".join(problems)
        _tell(brand, "⚠️ Some drafts couldn't be made:\n" + "\n".join(telegram.esc(p) for p in problems))
    return msg


def image(slug: str, style_id: str, note: str = "") -> str:
    """One AI image on request (from /image or the Control Center)."""
    brand = load_brand(slug)
    try:
        draft_id = producer.make_draft(brand, on_demand=producer.on_demand_post(brand, style_id, note))
    except FRIENDLY_ERRORS as e:
        _tell(brand, f"⚠️ Couldn't make the image: {telegram.esc(e)}")
        raise
    d = producer.get_draft(brand, draft_id)
    sent = _send(brand, d)
    return f"Made AI image draft #{draft_id}{sent}. Cost about US${d['cost_usd']:.2f}."


def reel(slug: str, style_id: str, note: str = "") -> str:
    """One AI reel on request (from /reel or the Control Center)."""
    brand = load_brand(slug)
    post = producer.on_demand_post(brand, style_id, note)
    post["format"] = "gemini_reel"
    try:
        draft_id = producer.make_draft(brand, on_demand=post)
    except FRIENDLY_ERRORS as e:
        _tell(brand, f"⚠️ Couldn't make the reel: {telegram.esc(e)}")
        raise
    d = producer.get_draft(brand, draft_id)
    kind = "reel" if d["format"] == "gemini_reel" else "image (the reel couldn't be made — see the draft's note)"
    return f"Made {kind} draft #{draft_id}{_send(brand, d)}. Cost about US${d['cost_usd']:.2f}."


def regen(slug: str, draft_id: int, note: str = "") -> str:
    brand = load_brand(slug)
    try:
        producer.remake(brand, draft_id, note=note)
    except FRIENDLY_ERRORS as e:
        producer.decide(brand, draft_id, "regenerate_failed")
        from app.db import connect
        with connect() as conn:
            conn.execute("UPDATE drafts SET status = 'pending' WHERE id = ?", (draft_id,))
        _tell(brand, f"⚠️ Couldn't remake draft #{draft_id}: {telegram.esc(e)}")
        raise
    d = producer.get_draft(brand, draft_id)
    return f"Remade draft #{draft_id}{_send(brand, d)}."


def _send(brand, d) -> str:
    try:
        telegram.send_draft(brand, d)
        return " and sent it to Telegram"
    except telegram.TelegramError as e:
        log.warning("Draft #%d not sent to Telegram: %s", d["id"], e)
        return " (couldn't send it to Telegram — see the Drafts page)"


def _tell(brand, text: str) -> None:
    try:
        telegram.send_message(brand, text)
    except telegram.TelegramError:
        pass


def stats(slug: str, send: bool = True) -> str:
    """Fetch the latest numbers, update the lessons, and (optionally) send the weekly report."""
    brand = load_brand(slug)
    n = insights.collect(brand)
    les = insights.make_lessons(brand)
    if send:
        try:
            telegram.send_message(brand, telegram.format_stats(brand))
        except telegram.TelegramError as e:
            log.warning("Couldn't send the report: %s", e)
    return f"Updated numbers for {n} post(s)." + (f" New lessons: {les['headline']}" if les else
                                                  " Lessons need at least 3 posts with results.")


def collect_tick(slug: str) -> None:
    """Every night: refresh post numbers (no message)."""
    try:
        brand = load_brand(slug)
        if brand.secret("META_ACCESS_TOKEN"):
            insights.collect(brand)
    except Exception:
        log.exception("Collecting results failed")


def pause(slug: str) -> str:
    publisher.set_paused(load_brand(slug), True)
    return "Posting is paused. Nothing will be posted until you press Resume."


def resume(slug: str) -> str:
    publisher.set_paused(load_brand(slug), False)
    return "Posting resumed. Anything overdue goes out within a minute."


def publish_tick(slug: str) -> None:
    """Runs every minute: posts whatever is due. Never crashes the timer."""
    try:
        brand = load_brand(slug)
        if brand.secret("META_ACCESS_TOKEN"):
            publisher.publish_due(brand, telegram.notifier(brand))
    except Exception:
        log.exception("Posting check failed")


def month_plan(slug: str, which: str = "next") -> str:
    """The Planner's Content Calendar: next month (timer, 25th) or the next 30 days from tomorrow (button)."""
    from app import content_calendar
    brand = load_brand(slug)
    prepare_for_planning(slug)
    today = datetime.now(timezone(timedelta(hours=5, minutes=30))).date()
    if which == "next":
        start = content_calendar.next_month_start(today)
        days = content_calendar.month_days(start)
    else:
        start, days = today + timedelta(days=1), 30
    direction = ""
    from app.team import latest
    d = latest(brand, "direction")
    if d:
        direction = d["data"].get("plan_instructions", "")
    result = planner.make_plan(brand, start=start, note=direction, days=days)
    rows = planner.upcoming_plan(brand)
    try:
        telegram.send_message(brand, telegram.format_plan(brand, result["summary"],
                                                          [r for r in rows if r["when"].date() >= start],
                                                          result["cost_usd"]))
    except telegram.TelegramError as e:
        log.warning("Calendar not sent to Telegram: %s", e)
    kinds = {}
    for r in result["rows"]:
        k = {"gemini_reel": "reels", "story": "stories"}.get(r["format"], "posts")
        kinds[k] = kinds.get(k, 0) + 1
    return (f"Content calendar made for {start:%d %b} – {start + timedelta(days=days - 1):%d %b}: "
            + ", ".join(f"{n} {k}" for k, n in kinds.items()) + f". Cost about US${result['cost_usd']:.2f}.")


def calendar_redo(slug: str, plan_id: int, note: str = "") -> str:
    from app import content_calendar
    r = content_calendar.redo(load_brand(slug), plan_id, note)
    return f"Redone: “{r['hook']}”."


def draft_item(slug: str, plan_id: int) -> str:
    """Make the draft for one calendar item now (instead of waiting for the morning)."""
    from app.db import connect
    brand = load_brand(slug)
    with connect() as conn:
        row = conn.execute("SELECT * FROM content_plan WHERE brand = ? AND id = ?", (slug, plan_id)).fetchone()
    if not row or row["status"] != "planned":
        return "That item already has a draft (see the Drafts page)."
    if row["format"] == "story":
        return "Stories are made in the next step of the build — for now, post this one by hand."
    draft_id = producer.make_draft(brand, dict(row))
    d = producer.get_draft(brand, draft_id)
    return f"Made draft #{draft_id}{_send(brand, d)}. Cost about US${d['cost_usd']:.2f}."


def team_week(slug: str) -> str:
    """The weekly team meeting: research → results → manager's direction → plan → review → campaign plan."""
    from app.team import meeting
    return meeting.run(slug)


def research(slug: str) -> str:
    from app.team import researcher
    brand = load_brand(slug)
    r = researcher.run(brand)
    researcher.tell_owner(brand, r)
    return f"Market brief written: {r['data']['headline']} (about US${r['cost_usd']:.2f})."


def direction(slug: str) -> str:
    from app.team import manager
    brand = load_brand(slug)
    d = manager.direct(brand)
    manager.tell_owner(brand, d)
    return f"Manager's direction: {d['data']['headline']} (about US${d['cost_usd']:.2f})."


def team_report(slug: str) -> str:
    from app.team import manager
    r = manager.weekly_report(load_brand(slug))
    return f"Weekly report sent (about US${r['cost_usd']:.2f})."


def money(slug: str) -> str:
    """Morning numbers: Meta ads + Shopify sales → goals, alerts, and pause proposals for clear losers."""
    from app.team import finance, smo
    brand = load_brand(slug)
    m = finance.daily_tick(brand)
    new = smo.daily_tick(brand) if m["ads"]["spend"] else []
    roas = m["metrics"]["blended_roas"]
    msg = (f"Numbers updated: ad spend ₹{m['ads']['spend']:,.0f}, sales ₹{m['sales']['revenue']:,.0f} in the last "
           f"7 days" + (f", blended ROAS {roas}" if roas else "") + ".")
    if m["alerts"]:
        msg += f" {len(m['alerts'])} alert(s) sent to Telegram."
    if new:
        msg += f" {len(new)} ad change(s) proposed."
    if m["problems"]:
        msg += " Couldn't read: " + " | ".join(m["problems"])
    return msg


def ad_action(slug: str, pid: int, yes: bool = True) -> str:
    """Carry out (or decline) one ad change you answered, and tell you the result on Telegram."""
    from app.team import ad_actions
    brand = load_brand(slug)
    result = ad_actions.decide(brand, pid, yes)
    _tell(brand, telegram.esc(result))
    return result


def money_tick(slug: str) -> None:
    """Every morning, quietly (alerts only)."""
    try:
        brand = load_brand(slug)
        if brand.secret("META_ACCESS_TOKEN") or brand.secret("SHOPIFY_ADMIN_TOKEN") or brand.secret("SHOPIFY_CLIENT_ID"):
            start(slug, "money")
    except Exception:
        log.exception("Morning numbers failed")


ACTIONS = {"sync": sync, "plan": plan, "send_plan": send_plan, "update": update,
           "drafts": drafts, "image": image, "reel": reel, "regen": regen, "pause": pause, "resume": resume, "stats": stats,
           "team_week": team_week, "research": research, "direction": direction, "team_report": team_report,
           "money": money, "ad_action": ad_action, "month_plan": month_plan, "calendar_redo": calendar_redo,
           "draft_item": draft_item}


# ---------------------------------------------------------------- background running

def start(slug: str, action: str, **kwargs) -> bool:
    """Start an action in the background. False if it's already running."""
    with _lock:
        if (slug, action) in _running:
            return False
        _running.add((slug, action))
    _set(slug, action, "running", "Working…")
    threading.Thread(target=_run, args=(slug, action), kwargs=kwargs, name=f"job-{action}", daemon=True).start()
    return True


def _run(slug: str, action: str, **kwargs) -> None:
    try:
        log.info("Started: %s (%s)", action, slug)
        message = ACTIONS[action](slug, **kwargs)
        _set(slug, action, "done", message)
        log.info("Finished: %s — %s", action, message)
    except FRIENDLY_ERRORS as e:
        _set(slug, action, "failed", str(e))
        log.warning("Failed: %s — %s", action, e)
    except Exception as e:
        from app import secrets_store
        _set(slug, action, "failed", secrets_store.scrub(
            f"Something unexpected went wrong ({type(e).__name__}: {str(e)[:200]}). The full details are on the "
            "Activity page."))
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
        # The weekly plan is made by the whole team (research → manager → planner → review).
        _scheduler.add_job(start, CronTrigger(day_of_week=sched.get("plan_day", "sun"), hour=hh, minute=mm,
                                              timezone=tz), args=[slug, "team_week"], id=f"{slug}:plan")
        _scheduler.add_job(start, CronTrigger(hour=6, minute=15, timezone=tz), args=[slug, "sync"],
                           id=f"{slug}:sync")
        # Every morning, prepare drafts for posts due in the next 2 days, so you have time to approve.
        _scheduler.add_job(start, CronTrigger(hour=7, minute=30, timezone=tz), args=[slug, "drafts"],
                           kwargs={"days": 2}, id=f"{slug}:drafts")
        # Every night: refresh post numbers. Monday morning: lessons + weekly report on Telegram.
        _scheduler.add_job(collect_tick, CronTrigger(hour=23, minute=10, timezone=tz), args=[slug],
                           id=f"{slug}:collect")
        _scheduler.add_job(start, CronTrigger(day_of_week="mon", hour=9, minute=5, timezone=tz), args=[slug, "stats"],
                           id=f"{slug}:stats")
        # The 25th of every month: the Content Calendar for next month.
        _scheduler.add_job(start, CronTrigger(day=25, hour=hh, minute=mm, timezone=tz), args=[slug, "month_plan"],
                           kwargs={"which": "next"}, id=f"{slug}:month_plan")
        # Monday 09:30: the Manager's one-page report (goals, wins, problems, what's needed from you).
        _scheduler.add_job(start, CronTrigger(day_of_week="mon", hour=9, minute=30, timezone=tz),
                           args=[slug, "team_report"], id=f"{slug}:team_report")
        # Every morning 08:05: ad + sales numbers, goal lights, alerts, pause proposals for clear losers.
        _scheduler.add_job(money_tick, CronTrigger(hour=8, minute=5, timezone=tz), args=[slug], id=f"{slug}:money")
        # Every minute: post anything that's due.
        _scheduler.add_job(publish_tick, IntervalTrigger(minutes=1), args=[slug], id=f"{slug}:publish",
                           max_instances=1, coalesce=True)
        log.info("Timer set for %s: weekly plan every %s at %s, product sync daily 06:15, drafts daily 07:30",
                 slug, sched.get("plan_day"), sched.get("plan_time"))


def next_plan_time(slug: str) -> str | None:
    job = _scheduler.get_job(f"{slug}:plan") if _scheduler else None
    return job.next_run_time.strftime("%a %d %b, %I:%M %p") if job and job.next_run_time else None
