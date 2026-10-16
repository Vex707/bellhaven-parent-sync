"""SQLite state: every proposal ever made and what a reviewer decided about it.

The proposal key is the idempotency guarantee. A re-run that produces a key
already in the table (pending, approved, rejected, applied, failed) is skipped,
so decided items are never re-proposed.
"""
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from . import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS proposals (
    key TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    category TEXT NOT NULL,
    title TEXT NOT NULL,
    payload TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',   -- pending | applied | rejected | failed | superseded
    first_run_id INTEGER,
    created_at TEXT NOT NULL,
    decided_at TEXT,
    reviewer_note TEXT,
    result TEXT
);
CREATE TABLE IF NOT EXISTS runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at TEXT NOT NULL,
    locations INTEGER,
    accounts INTEGER,
    proposed INTEGER,
    new_proposals INTEGER,
    report TEXT
);
"""


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%SZ")


class Store:
    def __init__(self, path: Path | str | None = None):
        path = Path(path or config.DATA_DIR / "state.db")
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)

    def record_run(self, locations: int, accounts: int, proposals: list[dict], report: list[dict]) -> tuple[int, int]:
        cur = self.db.execute(
            "INSERT INTO runs (started_at, locations, accounts, proposed, report) VALUES (?,?,?,?,?)",
            (_now(), locations, accounts, len(proposals), json.dumps(report)))
        run_id = cur.lastrowid
        new = 0
        live_keys = {p["key"] for p in proposals}
        for p in proposals:
            cur = self.db.execute(
                "INSERT OR IGNORE INTO proposals (key, kind, category, title, payload, first_run_id, created_at) "
                "VALUES (?,?,?,?,?,?,?)",
                (p["key"], p["kind"], p["category"], p["title"], json.dumps(p), run_id, _now()))
            new += cur.rowcount
        # A pending proposal the latest run no longer produces is out of date (the CRM or
        # website changed underneath it). Retire it instead of letting someone approve it.
        pending = [r["key"] for r in self.db.execute("SELECT key FROM proposals WHERE status='pending'")]
        for key in pending:
            if key not in live_keys:
                self.db.execute("UPDATE proposals SET status='superseded', decided_at=?, result=? WHERE key=?",
                                (_now(), "No longer produced by the latest run", key))
        self.db.execute("UPDATE runs SET new_proposals=? WHERE id=?", (new, run_id))
        self.db.commit()
        return run_id, new

    def proposals(self, status: str | None = None) -> list[dict]:
        sql = "SELECT * FROM proposals" + (" WHERE status=?" if status else "") + " ORDER BY created_at, key"
        rows = self.db.execute(sql, (status,) if status else ()).fetchall()
        return [self._row(r) for r in rows]

    def get(self, key: str) -> dict | None:
        r = self.db.execute("SELECT * FROM proposals WHERE key=?", (key,)).fetchone()
        return self._row(r) if r else None

    def decide(self, key: str, status: str, reviewer_note: str = "", result: dict | None = None) -> None:
        self.db.execute("UPDATE proposals SET status=?, decided_at=?, reviewer_note=?, result=? WHERE key=?",
                        (status, _now(), reviewer_note, json.dumps(result) if result else None, key))
        self.db.commit()

    def latest_run(self) -> dict | None:
        r = self.db.execute("SELECT * FROM runs ORDER BY id DESC LIMIT 1").fetchone()
        if not r:
            return None
        d = dict(r)
        d["report"] = json.loads(d["report"] or "[]")
        return d

    @staticmethod
    def _row(r: sqlite3.Row) -> dict:
        d = dict(r)
        d["payload"] = json.loads(d["payload"])
        d["result"] = json.loads(d["result"]) if d["result"] else None
        return d
