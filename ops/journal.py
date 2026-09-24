"""Trade journal: every order, trade, veto and session, in one SQLite file (data/journal.db).

The single source of truth for what the system did. The tax export, the session report and
the kill-switch checks read from here.
"""

import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

DB = Path(os.environ.get("SISCO_DATA", "data")) / "journal.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS trades (
  id TEXT PRIMARY KEY,           -- clOrdId
  mode TEXT, strategy TEXT, params TEXT, coin TEXT, inst_id TEXT, side TEXT, kind TEXT,
  planned_entry REAL, entry_px REAL, stop REAL, target REAL, contracts REAL, ct_val REAL,
  one_r_usd REAL, risk_usd REAL, leverage INTEGER, time_limit_h REAL,
  signal_time TEXT, placed_at TEXT, filled_at TEXT, closed_at TEXT, checked_to_ms INTEGER,
  exit_px REAL, exit_reason TEXT, pnl_usd REAL, fee_usd REAL, funding_usd REAL,
  status TEXT                    -- pending | open | closed | cancelled
);
CREATE TABLE IF NOT EXISTS vetoes (time TEXT, strategy TEXT, coin TEXT, side TEXT, who TEXT, reason TEXT, card TEXT);
CREATE TABLE IF NOT EXISTS sessions (time TEXT, mode TEXT, report TEXT);
CREATE TABLE IF NOT EXISTS state (key TEXT PRIMARY KEY, value TEXT);
"""


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Journal:
    def __init__(self, path=DB):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)

    def get(self, key, default=None):
        r = self.db.execute("SELECT value FROM state WHERE key = ?", (key,)).fetchone()
        return json.loads(r["value"]) if r else default

    def set(self, key, value):
        with self.db:
            self.db.execute("INSERT OR REPLACE INTO state VALUES (?, ?)", (key, json.dumps(value)))

    def add_trade(self, **row):
        cols = ", ".join(row)
        with self.db:
            self.db.execute(f"INSERT INTO trades ({cols}) VALUES ({', '.join('?' * len(row))})", list(row.values()))

    def update(self, trade_id, **fields):
        with self.db:
            self.db.execute(f"UPDATE trades SET {', '.join(f'{k} = ?' for k in fields)} WHERE id = ?", [*fields.values(), trade_id])

    def trades(self, where="1=1", params=()):
        return [dict(r) for r in self.db.execute(f"SELECT * FROM trades WHERE {where} ORDER BY placed_at", params)]

    def veto(self, strategy, coin, side, who, reason, card=""):
        with self.db:
            self.db.execute("INSERT INTO vetoes VALUES (?, ?, ?, ?, ?, ?, ?)", (now(), strategy, coin, side, who, reason, card))

    def log_session(self, mode, report):
        with self.db:
            self.db.execute("INSERT INTO sessions VALUES (?, ?, ?)", (now(), mode, report))

    def r_multiple(self, t):
        """Realized result of a closed trade in R (1R at the time of the trade)."""
        return (t["pnl_usd"] - (t["fee_usd"] or 0) + (t["funding_usd"] or 0)) / t["one_r_usd"] if t["one_r_usd"] else 0.0
