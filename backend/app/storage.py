import json
import sqlite3
import time
from pathlib import Path


class Store:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.executescript("""
        CREATE TABLE IF NOT EXISTS jobs (
          id TEXT PRIMARY KEY, regid TEXT NOT NULL, status TEXT NOT NULL,
          created REAL NOT NULL, completed REAL, expires REAL, cache_key TEXT NOT NULL,
          progress TEXT NOT NULL, result TEXT, error TEXT);
        CREATE TABLE IF NOT EXISTS events (
          job TEXT NOT NULL, seq INTEGER NOT NULL, payload TEXT NOT NULL,
          PRIMARY KEY(job, seq));
        CREATE INDEX IF NOT EXISTS cache_jobs ON jobs(cache_key,status,expires);
        """)
        # A killed process cannot keep a job running forever. Its result is never cached.
        self.db.execute(
            "UPDATE jobs SET status='interrupted',error='Server restarted. Please run the analysis again.' WHERE status IN ('queued','running')"
        )
        self.db.commit()

    def create(self, job, regid, cache_key):
        progress = {"stage": "queued", "message": "Analysis queued", "done": 0, "total": None}
        self.db.execute(
            "INSERT INTO jobs(id,regid,status,created,cache_key,progress) VALUES(?,?,'queued',?,?,?)",
            (job, regid, time.time(), cache_key, json.dumps(progress)),
        )
        self.db.commit()
        self.event(job, progress)

    def event(self, job, progress):
        seq = self.db.execute(
            "SELECT COALESCE(MAX(seq),0)+1 FROM events WHERE job=?", (job,)
        ).fetchone()[0]
        payload = {**progress, "at": time.time()}
        self.db.execute(
            "INSERT INTO events VALUES(?,?,?)", (job, seq, json.dumps(payload, ensure_ascii=False))
        )
        self.db.execute(
            "UPDATE jobs SET progress=? WHERE id=?", (json.dumps(payload, ensure_ascii=False), job)
        )
        self.db.commit()
        return seq

    def get(self, job, result=False):
        row = self.db.execute("SELECT * FROM jobs WHERE id=?", (job,)).fetchone()
        if row is None:
            return None
        item = dict(row)
        item["progress"] = json.loads(item["progress"])
        item["cached"] = item["status"] == "complete" and (item["expires"] or 0) > time.time()
        item.pop("cache_key")
        if result:
            item["result"] = json.loads(item["result"]) if item["result"] else None
        else:
            item.pop("result")
        return item

    def cache(self, key):
        row = self.db.execute(
            "SELECT id FROM jobs WHERE cache_key=? AND status='complete' AND expires>? ORDER BY completed DESC LIMIT 1",
            (key, time.time()),
        ).fetchone()
        return self.get(row[0]) if row else None

    def set_status(self, job, status):
        self.db.execute("UPDATE jobs SET status=? WHERE id=?", (status, job))
        self.db.commit()

    def finish(self, job, result, ttl, partial=False):
        now = time.time()
        self.db.execute(
            "UPDATE jobs SET status=?,completed=?,expires=?,result=? WHERE id=?",
            (
                "partial" if partial else "complete",
                now,
                None if partial else now + ttl,
                json.dumps(result, ensure_ascii=False),
                job,
            ),
        )
        self.db.commit()

    def fail(self, job, message):
        self.db.execute(
            "UPDATE jobs SET status='failed',completed=?,error=? WHERE id=?",
            (time.time(), message, job),
        )
        self.db.commit()

    def events(self, job, after):
        return [
            (r[0], json.loads(r[1]))
            for r in self.db.execute(
                "SELECT seq,payload FROM events WHERE job=? AND seq>? ORDER BY seq", (job, after)
            )
        ]

    def recent(self):
        return [
            self.get(r[0])
            for r in self.db.execute("SELECT id FROM jobs ORDER BY created DESC LIMIT 20")
        ]

    def close(self):
        self.db.close()
