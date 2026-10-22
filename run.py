"""Start the agent.

    python run.py

What happens:
1. Creates your .env key file the first time (from .env.example).
2. Sets up the database (data/agent.db) and the daily log files (logs/).
3. Checks the brand folders in brands/.
4. Opens the Control Center at http://localhost:8000

It also starts the timer (daily product sync, weekly plan) and the Telegram
bot that answers your commands — all in this one always-on program.
"""

import logging
import os
import socket
import sys
import threading
import webbrowser

from app import secrets_store

secrets_store.load_env()  # must happen before anything reads a key

from app.brands import BrandConfigError, list_brands, load_brand  # noqa: E402
from app import jobs, renderer, telegram  # noqa: E402
from app.db import DB_PATH, init_db  # noqa: E402
from app.logging_setup import setup_logging  # noqa: E402
from app.web import create_app  # noqa: E402

log = logging.getLogger("run")


def already_running(port: int) -> bool:
    """True if something (usually another copy of the agent) is already using the port.
    Windows lets two programs share a port, and then the OLD one answers the browser."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(1)
        return s.connect_ex(("127.0.0.1", port)) == 0


def main() -> None:
    port = int(os.environ.get("PORT", 8000))
    if "PORT" not in os.environ and already_running(port):
        print("\n" + "=" * 64 + "\n  THE AGENT IS ALREADY RUNNING in another black window.\n"
              "  Close ALL black windows, then start the agent again from your newest folder.\n"
              "  (Two copies at once would make duplicate plans and posts.)\n" + "=" * 64 + "\n", flush=True)
        sys.exit(1)
    setup_logging()
    init_db()
    log.info("Database ready at %s", DB_PATH)

    brands = list_brands()
    if not brands:
        log.warning("No brands found. Expected brands/<name>/config.yaml")
    for slug in brands:
        try:
            brand = load_brand(slug)
            log.info("Brand loaded: %s (auto_publish=%s)", brand.name, brand.auto_publish)
        except BrandConfigError as e:
            log.error("Brand problem: %s", e)

    renderer.start_browser_install()  # first run: downloads the picture maker in the background
    jobs.start_scheduler()
    for slug in brands:
        telegram.Bot(lambda slug=slug: load_brand(slug)).start()

    # On a laptop, only this computer can open the page (127.0.0.1).
    # Hosts like Railway/Render set PORT, and then the page must be reachable from outside.
    on_host = "PORT" in os.environ
    host = os.environ.get("HOST", "0.0.0.0" if on_host else "127.0.0.1")
    url = f"http://localhost:{port}"
    log.info("Control Center: %s", url)
    print("\n" + "=" * 64 + f"\n  THE AGENT IS RUNNING.  Open {url} in your browser.\n"
          "  Keep this window open (you can minimise it). Closing it stops the agent.\n"
          "  New lines appear here only when something happens.\n" + "=" * 64 + "\n", flush=True)
    if not on_host and os.environ.get("NO_BROWSER") != "1":
        threading.Timer(1.5, lambda: webbrowser.open(url)).start()

    create_app().run(host=host, port=port, debug=False, use_reloader=False)


if __name__ == "__main__":
    main()
