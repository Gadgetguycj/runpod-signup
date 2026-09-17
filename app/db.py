"""SQLite storage. One connection per operation, WAL enabled."""

import os
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .emails import normalize_email

SCHEMA = """
CREATE TABLE IF NOT EXISTS links (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    url TEXT NOT NULL UNIQUE,
    claimed_by_entry_id INTEGER REFERENCES entries(id),
    claimed_at TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS entries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    raw_email TEXT NOT NULL,
    normalized_email TEXT NOT NULL UNIQUE,
    discord_username TEXT,
    link_id INTEGER REFERENCES links(id),
    created_at TEXT NOT NULL,
    link_claimed_at TEXT
);

CREATE TABLE IF NOT EXISTS allowed_emails (
    normalized_email TEXT PRIMARY KEY
);

CREATE TABLE IF NOT EXISTS visits (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,
    user_agent TEXT,
    referrer TEXT,
    ip_truncated TEXT,
    country TEXT
);

CREATE TABLE IF NOT EXISTS claim_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,
    ip_key TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS claim_events_recent ON claim_events(created_at);
CREATE INDEX IF NOT EXISTS claim_events_by_ip ON claim_events(ip_key, created_at);

CREATE UNIQUE INDEX IF NOT EXISTS links_one_owner
    ON links(claimed_by_entry_id) WHERE claimed_by_entry_id IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS entries_one_link
    ON entries(link_id) WHERE link_id IS NOT NULL;
"""


def data_dir() -> Path:
    return Path(os.environ.get("DATA_DIR", "/data"))


def db_path() -> Path:
    return data_dir() / "signup.db"


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def hour_ago() -> str:
    return (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat(timespec="seconds")


def connect() -> sqlite3.Connection:
    path = db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=15.0, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=15000")
    return conn


def init_db() -> None:
    conn = connect()
    try:
        conn.executescript(SCHEMA)
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(visits)")}
        if "country" not in columns:
            conn.execute("ALTER TABLE visits ADD COLUMN country TEXT")
    finally:
        conn.close()


def record_visit(
    user_agent: str | None,
    referrer: str | None,
    ip_truncated: str | None,
    country: str | None = None,
) -> None:
    conn = connect()
    try:
        conn.execute(
            "INSERT INTO visits (created_at, user_agent, referrer, ip_truncated, country)"
            " VALUES (?, ?, ?, ?, ?)",
            (now(), user_agent, referrer, ip_truncated, country),
        )
    finally:
        conn.close()


def allowlist_count() -> int:
    conn = connect()
    try:
        return conn.execute("SELECT COUNT(*) AS n FROM allowed_emails").fetchone()["n"]
    finally:
        conn.close()


def is_allowed(normalized: str) -> bool:
    """Everybody is allowed while the allowlist table is empty."""
    conn = connect()
    try:
        if conn.execute("SELECT COUNT(*) AS n FROM allowed_emails").fetchone()["n"] == 0:
            return True
        row = conn.execute(
            "SELECT 1 FROM allowed_emails WHERE normalized_email = ?", (normalized,)
        ).fetchone()
        return row is not None
    finally:
        conn.close()


def claim(
    raw_email: str,
    discord_username: str | None,
    ip_key: str = "unknown",
    per_ip_limit: int = 0,
    global_limit: int = 0,
    claims_open: bool = True,
) -> dict:
    """Record the entry and hand out at most one link, atomically.

    The whole read-modify-write runs inside one BEGIN IMMEDIATE transaction, so two
    simultaneous submits cannot take the same link and a burst cannot slip past the
    rate limit by all reading a stale count.

    Someone who already holds a link always gets it back. They are never rate limited
    and never counted, so reloading costs an attendee nothing. The gates below only
    decide whether a NEW link leaves the pool. An entry is always recorded either way,
    so the raffle is never affected and no visitor is ever refused.
    """
    normalized = normalize_email(raw_email)
    conn = connect()
    gate = None
    try:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT id, link_id FROM entries WHERE normalized_email = ?", (normalized,)
        ).fetchone()
        returning = row is not None
        if row is None:
            cur = conn.execute(
                "INSERT INTO entries (raw_email, normalized_email, discord_username, created_at)"
                " VALUES (?, ?, ?, ?)",
                (raw_email.strip(), normalized, (discord_username or "").strip() or None, now()),
            )
            entry_id = cur.lastrowid
            link_id = None
        else:
            entry_id = row["id"]
            link_id = row["link_id"]
            if discord_username and discord_username.strip():
                conn.execute(
                    "UPDATE entries SET discord_username = ? WHERE id = ? AND discord_username IS NULL",
                    (discord_username.strip(), entry_id),
                )

        if link_id is None:
            gate = _handout_gate(conn, ip_key, per_ip_limit, global_limit, claims_open)
        if link_id is None and gate is None:
            free = conn.execute(
                "SELECT id, url FROM links WHERE claimed_by_entry_id IS NULL ORDER BY id LIMIT 1"
            ).fetchone()
            if free is not None:
                stamp = now()
                conn.execute(
                    "UPDATE links SET claimed_by_entry_id = ?, claimed_at = ?"
                    " WHERE id = ? AND claimed_by_entry_id IS NULL",
                    (entry_id, stamp, free["id"]),
                )
                conn.execute(
                    "UPDATE entries SET link_id = ?, link_claimed_at = ? WHERE id = ?",
                    (free["id"], stamp, entry_id),
                )
                conn.execute(
                    "INSERT INTO claim_events (created_at, ip_key) VALUES (?, ?)",
                    (stamp, ip_key or "unknown"),
                )
                link_id = free["id"]
            else:
                gate = "pool_empty"

        link = None
        claimed_at = None
        if link_id is not None:
            found = conn.execute(
                "SELECT url, claimed_at FROM links WHERE id = ?", (link_id,)
            ).fetchone()
            link = found["url"]
            claimed_at = found["claimed_at"]
            gate = None
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    finally:
        conn.close()
    return {
        "entry_id": entry_id,
        "normalized_email": normalized,
        "link_url": link,
        "returning": returning,
        "link_claimed_at": claimed_at,
        "withheld_because": gate,
    }


def _handout_gate(conn, ip_key, per_ip_limit, global_limit, claims_open) -> str | None:
    """Why a new link must not leave the pool right now, or None to allow it.

    Runs inside the caller's BEGIN IMMEDIATE transaction.
    """
    if not claims_open:
        return "claims_closed"
    since = hour_ago()
    if global_limit > 0:
        total = conn.execute(
            "SELECT COUNT(*) AS n FROM claim_events WHERE created_at >= ?", (since,)
        ).fetchone()["n"]
        if total >= global_limit:
            return "global_limit"
    if per_ip_limit > 0:
        mine = conn.execute(
            "SELECT COUNT(*) AS n FROM claim_events WHERE ip_key = ? AND created_at >= ?",
            (ip_key or "unknown", since),
        ).fetchone()["n"]
        if mine >= per_ip_limit:
            return "ip_limit"
    return None


def claims_in_last_hour(ip_key: str | None = None) -> int:
    conn = connect()
    try:
        if ip_key is None:
            return conn.execute(
                "SELECT COUNT(*) AS n FROM claim_events WHERE created_at >= ?", (hour_ago(),)
            ).fetchone()["n"]
        return conn.execute(
            "SELECT COUNT(*) AS n FROM claim_events WHERE ip_key = ? AND created_at >= ?",
            (ip_key, hour_ago()),
        ).fetchone()["n"]
    finally:
        conn.close()


def busiest_addresses_last_hour(limit: int = 5) -> list[dict]:
    conn = connect()
    try:
        return [
            dict(row)
            for row in conn.execute(
                "SELECT ip_key, COUNT(*) AS claims FROM claim_events WHERE created_at >= ?"
                " GROUP BY ip_key ORDER BY claims DESC, ip_key LIMIT ?",
                (hour_ago(), limit),
            )
        ]
    finally:
        conn.close()


def import_links(text: str) -> dict:
    added = 0
    skipped = 0
    seen: set[str] = set()
    conn = connect()
    try:
        conn.execute("BEGIN IMMEDIATE")
        for line in text.splitlines():
            url = line.strip()
            if not url:
                continue
            if url in seen:
                skipped += 1
                continue
            seen.add(url)
            cur = conn.execute(
                "INSERT OR IGNORE INTO links (url, created_at) VALUES (?, ?)", (url, now())
            )
            if cur.rowcount:
                added += 1
            else:
                skipped += 1
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    finally:
        conn.close()
    return {"added": added, "skipped": skipped}


def import_allowed_emails(text: str) -> dict:
    added = 0
    skipped = 0
    seen: set[str] = set()
    conn = connect()
    try:
        conn.execute("BEGIN IMMEDIATE")
        for line in text.splitlines():
            if not line.strip():
                continue
            normalized = normalize_email(line)
            if not normalized or normalized in seen:
                skipped += 1
                continue
            seen.add(normalized)
            cur = conn.execute(
                "INSERT OR IGNORE INTO allowed_emails (normalized_email) VALUES (?)", (normalized,)
            )
            if cur.rowcount:
                added += 1
            else:
                skipped += 1
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    finally:
        conn.close()
    return {"added": added, "skipped": skipped}


def stats() -> dict:
    conn = connect()
    try:
        def one(sql: str) -> int:
            return conn.execute(sql).fetchone()["n"]

        total = one("SELECT COUNT(*) AS n FROM links")
        claimed = one("SELECT COUNT(*) AS n FROM links WHERE claimed_by_entry_id IS NOT NULL")
        return {
            "visits": one("SELECT COUNT(*) AS n FROM visits"),
            "entries": one("SELECT COUNT(*) AS n FROM entries"),
            "raffle_entries": one(
                "SELECT COUNT(*) AS n FROM entries WHERE discord_username IS NOT NULL"
            ),
            "entries_without_discord": one(
                "SELECT COUNT(*) AS n FROM entries WHERE discord_username IS NULL"
            ),
            "links_total": total,
            "links_claimed": claimed,
            "links_remaining": total - claimed,
        }
    finally:
        conn.close()


def export_entries() -> list[sqlite3.Row]:
    conn = connect()
    try:
        return conn.execute(
            "SELECT e.raw_email, e.normalized_email, e.discord_username,"
            " l.url AS claimed_link, e.created_at, e.link_claimed_at"
            " FROM entries e LEFT JOIN links l ON l.id = e.link_id ORDER BY e.id"
        ).fetchall()
    finally:
        conn.close()


def find_entry(normalized: str) -> dict | None:
    """The entry for a normalized address, with its link when one is held."""
    conn = connect()
    try:
        row = conn.execute(
            "SELECT e.raw_email, e.normalized_email, e.discord_username, l.url AS link_url"
            " FROM entries e LEFT JOIN links l ON l.id = e.link_id"
            " WHERE e.normalized_email = ?",
            (normalized,),
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()
