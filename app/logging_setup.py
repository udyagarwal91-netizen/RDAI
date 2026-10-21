"""Readable logs: one file per day in logs/, e.g. logs/2026-10-20.log.

Open any day's file in Notepad/TextEdit to see what the agent did.
A filter masks any API key before a line is written, so keys never
end up in log files.
"""

import logging
import sys
from datetime import datetime

from app import LOGS_DIR, secrets_store

FORMAT = "%(asctime)s  %(levelname)-7s  %(name)-22s  %(message)s"


class ScrubSecrets(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        message = record.getMessage()
        if record.exc_info:
            message += "\n" + logging.Formatter().formatException(record.exc_info)
            record.exc_info = None
            record.exc_text = None
        record.msg = secrets_store.scrub(message)
        record.args = None
        return True


class DailyFileHandler(logging.FileHandler):
    """Writes to logs/<today>.log and switches file at midnight."""

    def __init__(self):
        LOGS_DIR.mkdir(parents=True, exist_ok=True)
        self._day = self._today()
        super().__init__(LOGS_DIR / f"{self._day}.log", encoding="utf-8", delay=True)

    @staticmethod
    def _today() -> str:
        return datetime.now().strftime("%Y-%m-%d")

    def emit(self, record):
        today = self._today()
        if today != self._day:
            self.close()
            self._day = today
            self.baseFilename = str(LOGS_DIR / f"{today}.log")
        super().emit(record)


def setup_logging() -> None:
    # The Windows black window can't show ₹ or emoji by default; without this, a log line with ₹ prints a
    # scary "Logging error" instead. Switch it to UTF-8 and replace anything it still can't show.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    root = logging.getLogger()
    if any(isinstance(h, DailyFileHandler) for h in root.handlers):
        return  # already set up
    root.setLevel(logging.INFO)
    formatter = logging.Formatter(FORMAT, datefmt="%Y-%m-%d %H:%M:%S")
    for handler in (DailyFileHandler(), logging.StreamHandler()):
        handler.setFormatter(formatter)
        handler.addFilter(ScrubSecrets())
        root.addHandler(handler)
    # Libraries are chatty and can print full URLs (Telegram puts the token in the URL).
    for noisy in ("urllib3", "werkzeug"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
