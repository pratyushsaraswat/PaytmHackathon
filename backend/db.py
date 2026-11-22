"""SQLite storage (single file, zero setup).

Tables
  reports        - scam reports from users (the community-protection loop)
  score_history  - merchant Trust Score over time (shows 'your score went 62 -> 78')

DB path: env DB_PATH (default backend/data.db).  Free hosts wipe disk on restart, so
sample data is SEEDED on startup when the tables are empty -> the demo always has data.

Abuse safeguard: one report per reporter per key (reporter = hash of IP).  Set DEMO_REPEAT=1
to allow repeats while rehearsing.  Production would also weight by reporter reliability.
"""
import hashlib
import logging
import os
import re
import sqlite3
import time
from contextlib import closing

log = logging.getLogger("db")
DB_PATH = os.getenv("DB_PATH", os.path.join(os.path.dirname(__file__), "data.db"))
DEMO_REPEAT = os.getenv("DEMO_REPEAT", "") == "1"


def _conn():
    c = sqlite3.connect(DB_PATH, timeout=10)
    c.row_factory = sqlite3.Row
    return c


def norm_key(q):
    """Same scam ID typed differently -> same key."""
    q = q.strip().lower()
    q = re.sub(r"^https?://", "", q)
    q = re.sub(r"^www\.", "", q)
    if "@" not in q and "/" in q:
        q = q.split("/")[0]
    return q[:80]


def reporter_id(ip):
    return hashlib.sha256(("paytm-demo|" + (ip or "unknown")).encode()).hexdigest()[:16]


def init():
    with closing(_conn()) as c:
        c.executescript("""
        CREATE TABLE IF NOT EXISTS reports(
            id INTEGER PRIMARY KEY AUTOINCREMENT, key TEXT NOT NULL, reason TEXT,
            reporter TEXT NOT NULL, seeded INTEGER DEFAULT 0, created_at REAL NOT NULL);
        CREATE INDEX IF NOT EXISTS ix_reports_key ON reports(key);
        CREATE TABLE IF NOT EXISTS score_history(
            id INTEGER PRIMARY KEY AUTOINCREMENT, business TEXT NOT NULL, location TEXT NOT NULL,
            trust INTEGER NOT NULL, seeded INTEGER DEFAULT 0, created_at REAL NOT NULL);
        CREATE INDEX IF NOT EXISTS ix_hist ON score_history(business, location);
        """)
        c.commit()
    with closing(_conn()) as c:
        n_rep = c.execute("SELECT COUNT(*) FROM reports").fetchone()[0]
    seed()
    with closing(_conn()) as c:
        log.info("init ok path=%s reports=%d (before seed %d) history_rows=%d", DB_PATH,
                 c.execute("SELECT COUNT(*) FROM reports").fetchone()[0], n_rep,
                 c.execute("SELECT COUNT(*) FROM score_history").fetchone()[0])


def seed():
    now = time.time()
    day = 86400
    with closing(_conn()) as c:
        if c.execute("SELECT COUNT(*) FROM reports").fetchone()[0] == 0:
            rows = [("kbc-winner-claim@okaxis", "Fake lottery message", 3),
                    ("free-kyc-refund.in", "Fake KYC link asking for PIN", 2),
                    ("quickcash-loan@ybl", "Loan app harassing contacts", 4)]
            for key, reason, n in rows:
                for i in range(n):
                    c.execute("INSERT INTO reports(key,reason,reporter,seeded,created_at) VALUES(?,?,?,?,?)",
                              (key, reason, f"seed{i}", 1, now - i * day))
        if c.execute("SELECT COUNT(*) FROM score_history").fetchone()[0] == 0:
            for i, v in enumerate([58, 62, 66, 71, 74]):  # a merchant improving week by week
                c.execute("INSERT INTO score_history(business,location,trust,seeded,created_at) VALUES(?,?,?,?,?)",
                          ("sharma sweets", "andheri, mumbai", v, 1, now - (5 - i) * 7 * day))
        c.commit()


# ------------------------------------------------------------------ reports
def add_report(query, reason, reporter):
    """-> (added: bool, total_reports: int)"""
    key = norm_key(query)
    with closing(_conn()) as c:
        dup = c.execute("SELECT 1 FROM reports WHERE key=? AND reporter=?", (key, reporter)).fetchone()
        if dup and not DEMO_REPEAT:
            added = False
        else:
            c.execute("INSERT INTO reports(key,reason,reporter,created_at) VALUES(?,?,?,?)",
                      (key, (reason or "")[:200], reporter if not DEMO_REPEAT else f"{reporter}-{time.time()}", time.time()))
            c.commit()
            added = True
    total = count_reports(query)
    log.info("report key=%r added=%s total_reporters=%d reporter=%s", key, added, total, reporter[:6])
    return added, total


def count_reports(query):
    """Distinct reporters who reported this ID/domain."""
    with closing(_conn()) as c:
        return c.execute("SELECT COUNT(DISTINCT reporter) FROM reports WHERE key=?", (norm_key(query),)).fetchone()[0]


def reset_demo():
    """Remove non-seeded rows (rehearsal helper; only exposed when DEMO_REPEAT=1)."""
    with closing(_conn()) as c:
        c.execute("DELETE FROM reports WHERE seeded=0")
        c.execute("DELETE FROM score_history WHERE seeded=0")
        c.commit()
    log.warning("reset_demo: removed all non-seeded reports and history")


# ------------------------------------------------------------- score history
def add_score(business, location, trust):
    b, l = business.strip().lower(), location.strip().lower()
    now = time.time()
    with closing(_conn()) as c:
        last = c.execute("SELECT id, created_at FROM score_history WHERE business=? AND location=? "
                         "ORDER BY created_at DESC LIMIT 1", (b, l)).fetchone()
        if last and now - last["created_at"] < 600:  # re-runs within 10 min update, not clutter
            c.execute("UPDATE score_history SET trust=?, created_at=? WHERE id=?", (trust, now, last["id"]))
        else:
            c.execute("INSERT INTO score_history(business,location,trust,created_at) VALUES(?,?,?,?)", (b, l, trust, now))
        c.commit()


def history(business, location, limit=12):
    with closing(_conn()) as c:
        rows = c.execute("SELECT trust, created_at FROM score_history WHERE business=? AND location=? "
                         "ORDER BY created_at DESC LIMIT ?", (business.strip().lower(), location.strip().lower(), limit)).fetchall()
    return [{"trust": r["trust"], "t": r["created_at"]} for r in reversed(rows)]


# -------------------------------------------------------------------- admin
def dump(limit=200):
    """Read-only snapshot for the admin endpoint."""
    with closing(_conn()) as c:
        reports = [dict(r) for r in c.execute(
            "SELECT id, key, reason, seeded, datetime(created_at,'unixepoch','localtime') AS time "
            "FROM reports ORDER BY id DESC LIMIT ?", (limit,))]
        per_key = [dict(r) for r in c.execute(
            "SELECT key, COUNT(DISTINCT reporter) AS reporters, COUNT(*) AS rows_ FROM reports "
            "GROUP BY key ORDER BY reporters DESC")]
        hist = [dict(r) for r in c.execute(
            "SELECT business, location, trust, seeded, datetime(created_at,'unixepoch','localtime') AS time "
            "FROM score_history ORDER BY id DESC LIMIT ?", (limit,))]
    return {"reports": reports, "reports_per_id": per_key, "score_history": hist}