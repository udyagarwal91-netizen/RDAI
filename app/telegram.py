"""Talking to you on Telegram.

send_message() sends you a message. The Bot (started from run.py) listens
for your commands like /plan. It only ever answers YOUR chat (the chat ID
found during setup) — anyone else who finds the bot is ignored.
"""

import html
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


HELP = ("<b>Commands</b>\n/plan — this week's plan\n/help — this list\n\n"
        "Coming soon: /generate, /image, /reel, /queue, /pause, /resume, /stats, /brand")


class Bot:
    """Listens for your Telegram commands in the background."""

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
                                allowed_updates=["message"])
                for u in updates:
                    self._offset = u["update_id"] + 1
                    self.handle(brand, u.get("message") or {}, chat_id)
            except TelegramError as e:
                log.warning("Telegram bot: %s", e)
                self._stop.wait(15)
            except Exception:
                log.exception("Telegram bot: unexpected problem")
                self._stop.wait(15)

    def handle(self, brand: Brand, message: dict, chat_id: str) -> None:
        if str((message.get("chat") or {}).get("id")) != str(chat_id):
            return  # not you — ignore
        command = (message.get("text") or "").strip().split()[0].split("@")[0].lower() if message.get("text") else ""
        log.info("Telegram command: %s", command or "(non-text)")
        if command == "/plan":
            from app.planner import upcoming_plan
            send_message(brand, format_plan(brand, None, upcoming_plan(brand)))
        elif command in ("/start", "/help"):
            send_message(brand, HELP)
        elif command in ("/generate", "/image", "/reel", "/queue", "/pause", "/resume", "/stats", "/brand"):
            send_message(brand, f"{esc(command)} is coming in a later phase. For now: /plan")
        elif command:
            send_message(brand, "I didn't understand that. " + HELP)
