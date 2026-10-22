"""Talking to you on Telegram.

send_message() sends you a message. The Bot (started from run.py) listens
for your commands like /plan. It only ever answers YOUR chat (the chat ID
found during setup) — anyone else who finds the bot is ignored.
"""

import html
import json
import logging
import threading
import time

import requests

from app.brands import Brand

log = logging.getLogger(__name__)
API = "https://api.telegram.org/bot{token}/{method}"
MAX_LEN = 4000  # Telegram's limit is 4096 characters per message


class TelegramError(Exception):
    pass


def _call(token: str, method: str, **payload) -> dict:
    try:
        r = requests.post(API.format(token=token, method=method), json=payload, timeout=payload.get("timeout", 0) + 20)
    except requests.RequestException as e:
        # Never put the exception text in logs: it contains the URL, and the URL contains the token.
        raise TelegramError(f"Couldn't reach Telegram ({type(e).__name__}).") from None
    body = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
    if not body.get("ok"):
        raise TelegramError(body.get("description") or f"Telegram error {r.status_code}")
    return body["result"]


def send_message(brand: Brand, text: str) -> None:
    """Send an HTML-formatted message to the owner, split into parts if long."""
    token, chat_id = brand.secret("TELEGRAM_BOT_TOKEN"), brand.secret("TELEGRAM_CHAT_ID")
    if not (token and chat_id):
        raise TelegramError("Telegram isn't connected yet (Connect accounts → Telegram).")
    for part in _split(text):
        _call(token, "sendMessage", chat_id=chat_id, text=part, parse_mode="HTML", disable_web_page_preview=True)


def send_buttons(brand: Brand, text: str, buttons: list[tuple[str, str]] | None = None) -> None:
    """A message with a row of buttons (label, callback code)."""
    if not buttons:
        send_message(brand, text)
        return
    token, chat_id = brand.secret("TELEGRAM_BOT_TOKEN"), brand.secret("TELEGRAM_CHAT_ID")
    if not (token and chat_id):
        raise TelegramError("Telegram isn't connected yet (Connect accounts → Telegram).")
    _call(token, "sendMessage", chat_id=chat_id, text=text[:MAX_LEN], parse_mode="HTML", disable_web_page_preview=True,
          reply_markup={"inline_keyboard": [[{"text": label, "callback_data": data} for label, data in buttons]]})


def notifier(brand: Brand):
    """A notify(text, buttons) function for the publisher that never crashes it."""
    def notify(text: str, buttons=None) -> None:
        try:
            send_buttons(brand, esc(text), buttons)
        except TelegramError as e:
            log.warning("Couldn't send Telegram alert: %s", e)
    return notify


def format_queue(brand: Brand) -> str:
    from app import publisher

    groups = publisher.queue(brand)
    head = "⏸ <b>Posting is PAUSED</b> — send /resume to switch it back on.\n\n" if publisher.is_paused(brand) else ""
    if not groups:
        return head + "📭 Nothing is waiting to be posted. Approve a draft and it will appear here."
    lines = [head + "🗓 <b>Waiting to be posted</b>"]
    icons = {"queued": "⏳", "publishing": "📤", "failed": "❌", "missed": "⏰"}
    for g in groups:
        bits = ", ".join(f"{icons.get(p['status'], '')} {publisher.PLATFORM_NAMES[p['platform']]}" for p in g["posts"])
        lines.append(f"• <b>{g['when']:%a %d %b, %I:%M %p}</b> — draft #{g['draft_id']} ({bits})")
    return "\n".join(lines)


def format_stats(brand: Brand, days: int = 7) -> str:
    from app import insights

    rep = insights.report(brand, days)
    if not rep["rows"]:
        return (f"📊 No results yet for the last {days} days. Numbers appear once posts have been live for a while "
                "(they're collected every night).")
    names = {"instagram": "Instagram", "facebook": "Facebook"}
    lines = [f"📊 <b>{esc(brand.name)} — last {days} days</b>"]
    for platform, t in rep["totals"].items():
        lines.append(f"{names.get(platform, platform)}: {t['posts']} posts · reach {t['reach']:,} · "
                     f"{t['interactions']:,} likes/comments/saves/shares")
    if rep["best"]:
        lines.append("\n<b>Best posts</b>")
        for r in rep["best"]:
            lines.append(f"🏆 {r['when']:%a %d %b} {names.get(r['platform'], '')} · {esc(r['format'].replace('_', ' '))} · "
                         f"reach {r['reach'] or 0:,} · {100 * r['rate']:.1f}% engaged · {r['saves'] or 0} saves"
                         + (f"\n{esc(r['permalink'])}" if r["permalink"] else ""))
    if rep["by_format"]:
        lines.append("\n<b>By format</b> (engagement)")
        lines += [f"• {esc(g['name'])}: {100 * (g['avg_rate'] or 0):.1f}% over {g['posts']} post(s)" for g in rep["by_format"]]
    les = insights.lessons(brand)
    if les:
        lines.append(f"\n💡 <b>{esc(les['headline'])}</b>")
        lines += [f"✅ {esc(w)}" for w in les["worked"]] + [f"👉 {esc(t)}" for t in les["try_next"]]
        lines.append("<i>The next plan follows these lessons.</i>")
    return "\n".join(lines)


def _split(text: str) -> list[str]:
    parts, current = [], ""
    for block in text.split("\n\n"):
        if len(current) + len(block) + 2 > MAX_LEN and current:
            parts.append(current)
            current = ""
        current = f"{current}\n\n{block}" if current else block
    if current:
        parts.append(current)
    return parts


def esc(text) -> str:
    return html.escape(str(text or ""), quote=False)


def format_plan(brand: Brand, summary: str | None, rows: list[dict], cost_usd: float | None = None) -> str:
    """The plan as a readable Telegram message."""
    from app.planner import price_text  # avoid an import loop

    if not rows:
        return f"📅 <b>{esc(brand.name)}</b>\nNo posts planned yet. Send /plan after making a plan in the Control Center."
    first, last = rows[0]["when"], rows[-1]["when"]
    head = f"📅 <b>{esc(brand.name)} — plan for {first:%d %b} to {last:%d %b}</b>"
    if summary:
        head += f"\n<i>{esc(summary)}</i>"
    blocks = [head]
    for r in rows:
        when = r["when"].strftime("%a %d %b, %I:%M %p").replace(" 0", " ")
        lines = [f"<b>{esc(when)}</b> · {esc(r['format_label'])} · {esc(r['theme'].title())}"]
        for p in r["products"]:
            pack = f" ({p['pack_size']}-pc)" if p.get("pack_size") else ""
            lines.append(f"🛍 {esc(p['title'])}{esc(pack)} — {esc(price_text(p))}")
        lines.append(f"💡 {esc(r['idea'])}")
        if r.get("hook"):
            lines.append(f"🪝 “{esc(r['hook'])}”")
        for w in r.get("warnings", []):
            lines.append(f"⚠️ {esc(w)}")
        if r["status"] != "planned":
            lines.append(f"<i>status: {esc(r['status'])}</i>")
        blocks.append("\n".join(lines))
    footer = "Nothing is posted yet. Drafts with Approve buttons come in the next phase."
    if cost_usd is not None:
        footer += f"\n<i>Planning cost about US${cost_usd:.2f}.</i>"
    blocks.append(footer)
    return "\n\n".join(blocks)


HELP = ("<b>Commands</b>\n/plan — this week's plan\n/generate — make drafts for the coming week now\n"
        "/image <i>product</i> — one AI image, e.g. <code>/image active brief 3</code>\n"
        "/queue — what's waiting to be posted\n/pause — stop all posting\n/resume — start posting again\n"
        "/stats — last 7 days performance\n/help — this list\n\nComing soon: /reel, /brand")


# ---------------------------------------------------------------- drafts

def _call_files(token: str, method: str, data: dict, files: dict) -> dict:
    """Like _call, but uploads photos from disk."""
    try:
        r = requests.post(API.format(token=token, method=method), data=data, files=files, timeout=120)
    except requests.RequestException as e:
        raise TelegramError(f"Couldn't reach Telegram ({type(e).__name__}).") from None
    body = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
    if not body.get("ok"):
        raise TelegramError(body.get("description") or f"Telegram error {r.status_code}")
    return body["result"]


def format_draft(brand: Brand, d: dict, header: str = "📝") -> str:
    from app.planner import price_text

    when = d["when"].strftime("%a %d %b, %I:%M %p").replace(" 0", " ") if d["when"] else "not scheduled yet"
    lines = [f"{header} <b>Draft #{d['id']}</b> · {esc(when)} · {esc(d['format_label'])} · {esc((d['theme'] or '').title())}"]
    for p in d["products"][:1]:
        pack = f" ({p['pack_size']}-pc)" if p.get("pack_size") else ""
        lines.append(f"🛍 {esc(p['title'])}{esc(pack)} — {esc(price_text(p))}")
    if d.get("image_style") and d["image_style"] != "template":
        lines.append(f"<i>AI image style: {esc(d['image_style'].replace('_', ' '))}</i>")
    text = "\n".join(lines) + f"\n\n{esc(d['caption'])}"
    if d["warnings"]:
        text += "\n\n" + "\n".join(f"⚠️ {esc(w)}" for w in d["warnings"])
    text += f"\n\n<i>Cost about US${d['cost_usd']:.2f}" + (f" · version {d['attempt']}" if d["attempt"] > 1 else "") + "</i>"
    return text


def draft_buttons(draft_id: int) -> dict:
    return {"inline_keyboard": [
        [{"text": "✅ Approve", "callback_data": f"a:{draft_id}"}, {"text": "🔁 Regenerate", "callback_data": f"r:{draft_id}"}],
        [{"text": "✏️ Edit caption", "callback_data": f"e:{draft_id}"}, {"text": "❌ Reject", "callback_data": f"x:{draft_id}"}],
    ]}


def send_draft(brand: Brand, d: dict) -> None:
    """Photo(s) first, then the caption with the four buttons."""
    token, chat_id = brand.secret("TELEGRAM_BOT_TOKEN"), brand.secret("TELEGRAM_CHAT_ID")
    if not (token and chat_id):
        raise TelegramError("Telegram isn't connected yet (Connect accounts → Telegram).")
    files = [f for f in d["files"] if f.exists()]
    if len(files) == 1:
        with open(files[0], "rb") as fh:
            _call_files(token, "sendPhoto", {"chat_id": chat_id}, {"photo": fh})
    elif files:
        handles = {f"p{i}": open(f, "rb") for i, f in enumerate(files[:10])}
        try:
            media_json = json.dumps([{"type": "photo", "media": f"attach://{k}"} for k in handles])
            _call_files(token, "sendMediaGroup", {"chat_id": chat_id, "media": media_json}, handles)
        finally:
            for fh in handles.values():
                fh.close()
    header = "⚠️" if d["warnings"] else "📝"
    extra = {"reply_markup": draft_buttons(d["id"])} if d["status"] == "pending" else {}
    msg = _call(token, "sendMessage", chat_id=chat_id, text=format_draft(brand, d, header)[:MAX_LEN],
                parse_mode="HTML", disable_web_page_preview=True, **extra)
    from app.db import connect
    with connect() as conn:
        conn.execute("UPDATE drafts SET telegram_msg_id = ? WHERE id = ?", (str(msg["message_id"]), d["id"]))


class Bot:
    """Listens for your Telegram commands and button taps in the background."""

    def __init__(self, get_brand):
        self._get_brand = get_brand  # function returning the current Brand (re-read so settings changes apply)
        self._stop = threading.Event()
        self._offset = None

    def start(self) -> None:
        threading.Thread(target=self._run, name="telegram-bot", daemon=True).start()

    def stop(self) -> None:
        self._stop.set()

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                brand = self._get_brand()
                token, chat_id = brand.secret("TELEGRAM_BOT_TOKEN"), brand.secret("TELEGRAM_CHAT_ID")
                if not (token and chat_id):
                    self._stop.wait(30)  # not set up yet; check again later
                    continue
                updates = _call(token, "getUpdates", timeout=25, offset=self._offset,
                                allowed_updates=["message", "callback_query"])
                for u in updates:
                    self._offset = u["update_id"] + 1
                    try:
                        if "callback_query" in u:
                            self.handle_button(brand, u["callback_query"], chat_id)
                        else:
                            self.handle(brand, u.get("message") or {}, chat_id)
                    except TelegramError as e:
                        log.warning("Telegram bot: %s", e)
            except TelegramError as e:
                log.warning("Telegram bot: %s", e)
                self._stop.wait(15)
            except Exception:
                log.exception("Telegram bot: unexpected problem")
                self._stop.wait(15)

    # -------- text messages and commands

    def handle(self, brand: Brand, message: dict, chat_id: str) -> None:
        from app import jobs, producer
        from app.db import get_state, set_state

        if str((message.get("chat") or {}).get("id")) != str(chat_id):
            return  # not you — ignore
        text = (message.get("text") or "").strip()
        command = text.split()[0].split("@")[0].lower() if text.startswith("/") else ""
        log.info("Telegram %s", f"command: {command}" if command else "message")

        regen_id = get_state(brand.slug, "awaiting_regen")
        if regen_id and ((text and not command) or command == "/skip"):
            self._regenerate(brand, int(regen_id), "" if command == "/skip" else text)
            return
        awaiting = get_state(brand.slug, "awaiting_caption")
        if awaiting and text and not command:
            self._new_caption(brand, int(awaiting), text)
            return
        if command in ("/cancel", "/skip"):
            set_state(brand.slug, "awaiting_caption", "")
            set_state(brand.slug, "awaiting_regen", "")
            send_message(brand, "OK, cancelled.")
        elif command == "/plan":
            from app.planner import upcoming_plan
            send_message(brand, format_plan(brand, None, upcoming_plan(brand)))
        elif command == "/generate":
            started = jobs.start(brand.slug, "drafts")
            send_message(brand, "🛠 Making drafts for the coming week — they'll arrive here one by one."
                         if started else "Already making drafts — hang on.")
        elif command == "/image":
            query = text[len("/image"):].strip()
            style = producer.find_style(brand, query) if query else None
            if not style:
                from app.planner import catalog_styles
                names = "\n".join(f"• {esc(s['title'])}" for s in catalog_styles(brand))
                send_message(brand, f"Which product? Send e.g. <code>/image active brief 3</code>\n\n{names}")
                return
            started = jobs.start(brand.slug, "image", style_id=style["id"])
            send_message(brand, f"🎨 Making an AI image of <b>{esc(style['title'])}</b> (about a minute)…"
                         if started else "Already making an image — hang on.")
        elif command == "/queue":
            send_message(brand, format_queue(brand))
        elif command in ("/pause", "/resume"):
            from app import publisher
            publisher.set_paused(brand, command == "/pause")
            send_message(brand, "⏸ Posting paused. Nothing will be posted until you send /resume." if command == "/pause"
                         else "▶️ Posting resumed. Anything overdue goes out within a minute.")
        elif command == "/stats":
            send_message(brand, format_stats(brand))
        elif command in ("/start", "/help"):
            send_message(brand, HELP)
        elif command in ("/reel", "/brand"):
            send_message(brand, f"{esc(command)} is coming in a later phase. For now: /plan, /generate, /image")
        elif text:
            send_message(brand, "I didn't understand that. " + HELP)

    def _post_button(self, brand: Brand, q: dict, token: str, action: str, post_id: int) -> None:
        from app import publisher
        from app.db import connect

        with connect() as conn:
            post = conn.execute("SELECT * FROM posts WHERE id = ? AND brand = ?", (post_id, brand.slug)).fetchone()
        if not post or post["status"] not in ("failed", "missed"):
            _call(token, "answerCallbackQuery", callback_query_id=q["id"],
                  text="Already handled." if post else "That post no longer exists.")
            return
        _call(token, "answerCallbackQuery", callback_query_id=q["id"])
        name = publisher.PLATFORM_NAMES[post["platform"]]
        if action == "p":
            publisher.retry_now(brand, post_id)
            send_message(brand, f"📤 Posting draft #{post['draft_id']} to {name} within a minute…")
        else:
            publisher.cancel(brand, post_id)
            send_message(brand, f"OK — draft #{post['draft_id']} won't be posted to {name}.")

    def _regenerate(self, brand: Brand, draft_id: int, note: str) -> None:
        from app import jobs, producer
        from app.db import set_state

        set_state(brand.slug, "awaiting_regen", "")
        d = producer.get_draft(brand, draft_id)
        if not d or d["status"] != "pending":
            send_message(brand, f"Draft #{draft_id} is {d['status'] if d else 'gone'} — nothing to remake.")
            return
        if not jobs.start(brand.slug, "regen", draft_id=draft_id, note=note[:500]):
            send_message(brand, "Already remaking a draft — try again in a minute.")
            return
        producer.decide(brand, draft_id, "regenerate", note or None)
        send_message(brand, f"🔁 Making a new version of draft #{draft_id}" + (f" with: <i>{esc(note)}</i>" if note else "")
                     + " (about a minute)…")

    def _new_caption(self, brand: Brand, draft_id: int, text: str) -> None:
        from app import factcheck, producer, writer
        from app.db import set_state

        set_state(brand.slug, "awaiting_caption", "")
        d = producer.get_draft(brand, draft_id)
        if not d:
            send_message(brand, "That draft no longer exists.")
            return
        _, prices, packs = writer.product_facts(d["products"])
        tags = " ".join(json.loads(d["hashtags"] or "[]"))
        caption = text if "#" in text else (text + ("\n\n" + tags if tags else ""))
        problems = factcheck.check_text(caption, brand, prices, packs)
        producer.decide(brand, draft_id, "edit_caption", caption)
        d = producer.get_draft(brand, draft_id)
        d["warnings"] = problems
        if problems:
            send_message(brand, "Saved your caption, but please double-check: " + "; ".join(esc(p) for p in problems))
        send_draft(brand, d)

    # -------- button taps

    def handle_button(self, brand: Brand, q: dict, chat_id: str) -> None:
        from app import jobs, producer
        from app.db import set_state

        token = brand.secret("TELEGRAM_BOT_TOKEN")
        if str((q.get("from") or {}).get("id")) != str(chat_id) and \
                str(((q.get("message") or {}).get("chat") or {}).get("id")) != str(chat_id):
            return  # not you — ignore
        action, _, raw_id = (q.get("data") or "").partition(":")
        if action in ("p", "s"):  # post-level buttons: post now / try again, skip
            self._post_button(brand, q, token, action, int(raw_id) if raw_id.isdigit() else 0)
            return
        draft_id = int(raw_id) if raw_id.isdigit() else 0
        d = producer.get_draft(brand, draft_id)
        if not d:
            _call(token, "answerCallbackQuery", callback_query_id=q["id"], text="That draft no longer exists.")
            return
        if d["status"] != "pending" and action != "e":
            _call(token, "answerCallbackQuery", callback_query_id=q["id"], text=f"Already {d['status']}.")
            return
        _call(token, "answerCallbackQuery", callback_query_id=q["id"])
        msg = q.get("message") or {}
        if msg.get("message_id") and action in ("a", "x"):
            try:  # remove the buttons so nothing is pressed twice
                _call(token, "editMessageReplyMarkup", chat_id=chat_id, message_id=msg["message_id"],
                      reply_markup={"inline_keyboard": []})
            except TelegramError:
                pass
        if action == "a":
            when = producer.decide(brand, draft_id, "approve")
            from app import publisher
            paused = " Posting is paused right now — send /resume to switch it back on." if publisher.is_paused(brand) else ""
            send_message(brand, f"✅ Draft #{draft_id} approved. It will be posted on "
                                f"{esc(', '.join(publisher.PLATFORM_NAMES[p] for p in publisher.platforms(brand)))} "
                                f"on {when:%a %d %b at %I:%M %p}.{paused}")
        elif action == "x":
            producer.decide(brand, draft_id, "reject")
            send_message(brand, f"❌ Draft #{draft_id} rejected. It won't be posted.")
        elif action == "r":
            set_state(brand.slug, "awaiting_caption", "")
            set_state(brand.slug, "awaiting_regen", str(draft_id))
            send_message(brand, f"🔁 What should be different in draft #{draft_id}? Type it as your next message, e.g. "
                                "<i>gym bag flat-lay, no model, morning light</i> or <i>shorter caption, more Hinglish</i>.\n"
                                "Or send /skip to just make a fresh version.")
        elif action == "e":
            set_state(brand.slug, "awaiting_regen", "")
            set_state(brand.slug, "awaiting_caption", str(draft_id))
            send_message(brand, f"✏️ Send me the new caption for draft #{draft_id} as your next message. "
                                "Hashtags are kept unless you include your own. (/cancel to stop)")
