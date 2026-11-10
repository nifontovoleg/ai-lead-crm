import asyncio
import sqlite3
from datetime import datetime, timezone
from pathlib import Path


class DeliveryLedger:
    """Local delivery ledger only. Leads remain in Google Sheets."""

    def __init__(self, path):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        with self.connect() as db:
            db.execute("CREATE TABLE IF NOT EXISTS delivery (event TEXT PRIMARY KEY, state TEXT NOT NULL, updated TEXT NOT NULL)")
            db.execute("CREATE TABLE IF NOT EXISTS score_cache (event TEXT PRIMARY KEY, payload TEXT NOT NULL)")

    def connect(self):
        return sqlite3.connect(self.path)

    def claim(self, event):
        with self.connect() as db:
            cursor = db.execute("INSERT OR IGNORE INTO delivery VALUES (?, 'sending', ?)", (event, datetime.now(timezone.utc).isoformat()))
            return cursor.rowcount == 1

    def state(self, event):
        with self.connect() as db:
            row = db.execute("SELECT state FROM delivery WHERE event=?", (event,)).fetchone()
        return row[0] if row else None

    def finish(self, event, state):
        with self.connect() as db:
            db.execute("UPDATE delivery SET state=?, updated=? WHERE event=?", (state, datetime.now(timezone.utc).isoformat(), event))

    def cached_score(self, event):
        with self.connect() as db:
            row = db.execute("SELECT payload FROM score_cache WHERE event=?", (event,)).fetchone()
        return row[0] if row else None

    def cache_score(self, event, payload):
        with self.connect() as db:
            db.execute("INSERT OR REPLACE INTO score_cache VALUES (?, ?)", (event, payload))


class DemoNotifier:
    transport = "demo"

    def __init__(self, path):
        self.path = Path(path)

    def send(self, text):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as stream:
            stream.write(text + "\n\n---\n\n")


class TelegramNotifier:
    def __init__(self, settings):
        self.token = settings.telegram_token
        self.chat_id = settings.chat_id
        self.transport = f"telegram:{self.chat_id}"

    def send(self, text):
        from telegram import Bot, LinkPreviewOptions

        async def deliver():
            async with Bot(self.token) as bot:
                return await bot.send_message(chat_id=self.chat_id, text=text, parse_mode=None,
                                       link_preview_options=LinkPreviewOptions(is_disabled=True),
                                       read_timeout=15, write_timeout=15, connect_timeout=10)
        return asyncio.run(deliver())
