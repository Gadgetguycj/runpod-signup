"""A Discord username is held by one entry, the way the normalized email already is.

Without this one person enters the raffle many times by giving the same Discord name
with a fresh email address each time. Nothing here may cost anybody a credit link.
"""

import csv
import io
import re
import sqlite3

import pytest

from app import db
from app.emails import normalize_discord_username

from .conftest import rows, visible_text, walk

LINK = re.compile(r'class="linkbox" href="([^"]+)"')
REFUSED = (
    "That Discord username is already in the raffle under another email, so you are "
    "not entered under it."
)

# The schema as it stands on the live box, before the normalized username column.
LEGACY_SCHEMA = """
CREATE TABLE links (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    url TEXT NOT NULL UNIQUE,
    claimed_by_entry_id INTEGER REFERENCES entries(id),
    claimed_at TEXT,
    created_at TEXT NOT NULL
);
CREATE TABLE entries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    raw_email TEXT NOT NULL,
    normalized_email TEXT NOT NULL UNIQUE,
    discord_username TEXT,
    join_code TEXT,
    link_id INTEGER REFERENCES links(id),
    created_at TEXT NOT NULL,
    link_claimed_at TEXT
);
CREATE TABLE allowed_emails (normalized_email TEXT PRIMARY KEY);
CREATE TABLE visits (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,
    user_agent TEXT,
    referrer TEXT,
    ip_truncated TEXT,
    country TEXT
);
CREATE TABLE claim_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,
    ip_key TEXT NOT NULL
);
CREATE UNIQUE INDEX links_one_owner
    ON links(claimed_by_entry_id) WHERE claimed_by_entry_id IS NOT NULL;
CREATE UNIQUE INDEX entries_one_link ON entries(link_id) WHERE link_id IS NOT NULL;
"""


def load(client, auth, count=5):
    body = "\n".join(f"https://runpod.io/redeem/{n}" for n in range(count))
    assert client.post("/admin/links", headers=auth, content=body).status_code == 200


def submit(client, email, discord=""):
    """One visitor walking the form from the start, cookies and all."""
    client.cookies.clear()
    return walk(client, email, discord)


def stored(email):
    """The raw username and the normalized one held by this entry."""
    found = rows(
        None,
        "SELECT discord_username, normalized_discord_username FROM entries"
        " WHERE normalized_email = ?",
        (db.normalize_email(email),),
    )
    assert len(found) == 1
    return found[0]["discord_username"], found[0]["normalized_discord_username"]


def holders(normalized_username):
    return rows(
        None,
        "SELECT raw_email FROM entries WHERE normalized_discord_username = ?",
        (normalized_username,),
    )


def export_row(client, auth, email):
    export = client.get("/admin/entries.csv", headers=auth).text
    found = [r for r in csv.DictReader(io.StringIO(export)) if r["raw_email"] == email]
    assert len(found) == 1
    return found[0]


def insert_entry(conn, email, username, normalized_username, created_at="then"):
    conn.execute(
        "INSERT INTO entries (raw_email, normalized_email, discord_username,"
        " normalized_discord_username, created_at) VALUES (?, ?, ?, ?, ?)",
        (email, email, username, normalized_username, created_at),
    )


# ---------- 1. how a username is normalized ----------


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("  spaced_name  ", "spaced_name"),
        ("\tTabbed\n", "tabbed"),
        ("@atsign", "atsign"),
        ("  @Both_Of_Them  ", "both_of_them"),
        ("@@double", "@double"),
        ("MiXeDcAsE", "mixedcase"),
        ("mid@dle", "mid@dle"),
        ("", ""),
        ("@", ""),
    ],
)
def test_the_username_normalizer(raw, expected):
    assert normalize_discord_username(raw) == expected


@pytest.mark.parametrize(
    "one, two",
    [("a.b", "ab"), ("a_b", "ab"), ("a-b", "ab"), ("name1", "name_1")],
)
def test_two_real_people_are_not_folded_together(one, two):
    """A Discord name is not an email address, so dots and underscores count."""
    assert normalize_discord_username(one) != normalize_discord_username(two)


# ---------- 2. one holder per username ----------


def test_a_second_email_cannot_take_a_username_another_entry_holds(client, auth):
    load(client, auth)
    submit(client, "first@example.com", "shared_name")
    submit(client, "second@example.com", "@Shared_Name")

    assert stored("second@example.com") == (None, None)
    assert [row["raw_email"] for row in holders("shared_name")] == ["first@example.com"]
    row = export_row(client, auth, "second@example.com")
    assert row["discord_username"] == ""
    assert row["in_raffle"] == "no"
    assert client.get("/admin/stats", headers=auth).json()["raffle_entries"] == 1


def test_the_first_holder_keeps_the_username(client, auth):
    load(client, auth)
    submit(client, "first@example.com", "  Shared_Name  ")
    submit(client, "second@example.com", "shared_name")

    assert stored("first@example.com") == ("Shared_Name", "shared_name")
    row = export_row(client, auth, "first@example.com")
    assert row["discord_username"] == "Shared_Name", "the export lost the raw spelling"
    assert row["in_raffle"] == "yes"


def test_a_run_of_fresh_emails_cannot_stack_one_username(client, auth):
    """The abuse this change exists to stop."""
    load(client, auth)
    for n in range(5):
        submit(client, f"throwaway{n}@example.com", "stacker")
    assert len(holders("stacker")) == 1
    assert client.get("/admin/stats", headers=auth).json()["raffle_entries"] == 1


# ---------- 3. the credit is never blocked ----------


def test_the_refused_person_still_gets_a_credit_link(client, auth):
    load(client, auth)
    first = submit(client, "first@example.com", "shared_name")
    second = submit(client, "second@example.com", "@Shared_Name")

    assert LINK.search(second.text) is not None, "the refused person got no credit link"
    assert LINK.search(first.text).group(1) != LINK.search(second.text).group(1)

    again = submit(client, "second@example.com", "@Shared_Name")
    assert LINK.search(again.text).group(1) == LINK.search(second.text).group(1)
    assert client.get("/admin/stats", headers=auth).json()["links_claimed"] == 2


# ---------- 4. what the refused person reads ----------


def test_the_refused_person_is_told_on_the_result_page(client, auth):
    load(client, auth)
    submit(client, "holder@example.com", "shared_name")
    copy = visible_text(submit(client, "refused@example.com", "@Shared_Name").text)

    assert REFUSED in copy
    assert "holder@example.com" not in copy, "the page named the entry that holds it"


def test_the_refused_person_is_told_on_the_empty_pool_page(client):
    submit(client, "holder@example.com", "shared_name")
    copy = visible_text(submit(client, "refused@example.com", "shared_name").text)

    assert "You are on the list" in copy
    assert REFUSED in copy
    assert "holder@example.com" not in copy, "the page named the entry that holds it"


def test_nobody_else_reads_the_refusal_sentence(client, auth):
    load(client, auth)
    assert REFUSED not in visible_text(submit(client, "holder@example.com", "free_name").text)
    assert REFUSED not in visible_text(submit(client, "quiet@example.com").text)
    assert REFUSED not in visible_text(submit(client, "holder@example.com", "free_name").text)


# ---------- 5. re-submitting, correcting, leaving it blank ----------


def test_resubmitting_your_own_username_is_unaffected(client, auth):
    load(client, auth)
    first = submit(client, "owner@example.com", "owner_name")
    again = submit(client, "owner@example.com", "@Owner_Name")
    copy = visible_text(again.text)

    assert stored("owner@example.com") == ("owner_name", "owner_name")
    assert REFUSED not in copy
    assert "Your Discord username owner_name is in the raffle for RunPod swag." in copy
    assert LINK.search(again.text).group(1) == LINK.search(first.text).group(1)


def test_correcting_to_a_free_username_releases_the_old_one(client, auth):
    load(client, auth)
    submit(client, "mover@example.com", "old_name")
    submit(client, "mover@example.com", "new_name")
    assert stored("mover@example.com") == ("new_name", "new_name")
    assert holders("old_name") == [], "the old username was not released"

    taker = submit(client, "other@example.com", "old_name")
    assert stored("other@example.com") == ("old_name", "old_name")
    assert REFUSED not in visible_text(taker.text)


@pytest.mark.parametrize("blank", ["", "   ", "@"])
def test_a_blank_submit_changes_nothing(client, auth, blank):
    load(client, auth)
    submit(client, "keeper@example.com", "keeper_name")
    again = submit(client, "keeper@example.com", blank)

    assert stored("keeper@example.com") == ("keeper_name", "keeper_name")
    assert REFUSED not in visible_text(again.text)


def test_several_entries_with_no_username_coexist(client, auth):
    load(client, auth)
    for n in range(4):
        submit(client, f"nameless{n}@example.com")

    assert client.get("/admin/stats", headers=auth).json()["entries_without_discord"] == 4
    empty = rows(
        None,
        "SELECT COUNT(*) AS n FROM entries WHERE normalized_discord_username IS NULL",
    )
    assert empty[0]["n"] == 4


# ---------- 6. the database enforces it, not only the Python ----------


def test_the_unique_index_is_in_the_database(data_dir):
    names = {
        row["name"]
        for row in rows(
            None,
            "SELECT name FROM sqlite_master WHERE type = 'index' AND tbl_name = 'entries'",
        )
    }
    assert "entries_one_discord_username" in names


def test_the_index_rejects_a_direct_duplicate_insert(data_dir):
    conn = db.connect()
    try:
        insert_entry(conn, "a@example.com", "Dup_Name", "dup_name")
        with pytest.raises(sqlite3.IntegrityError):
            insert_entry(conn, "b@example.com", "dup_name", "dup_name")
    finally:
        conn.close()


def test_the_index_allows_any_number_of_rows_with_no_username(data_dir):
    conn = db.connect()
    try:
        for n in range(3):
            insert_entry(conn, f"none{n}@example.com", None, None)
        for n in range(3):
            insert_entry(conn, f"empty{n}@example.com", None, "")
    finally:
        conn.close()
    assert rows(None, "SELECT COUNT(*) AS n FROM entries")[0]["n"] == 6


# ---------- 7. the live database, which already holds entries ----------


def legacy_db(tmp_path):
    """A database on the current schema with two rows that collide once normalized.

    The colliding rows are inserted newest first, so an implementation that keeps the
    lowest id rather than the oldest row fails here.
    """
    conn = sqlite3.connect(tmp_path / "signup.db")
    conn.executescript(LEGACY_SCHEMA)
    conn.execute("INSERT INTO links (url, created_at) VALUES ('https://runpod.io/live', 'then')")
    conn.executemany(
        "INSERT INTO entries (raw_email, normalized_email, discord_username, created_at)"
        " VALUES (?, ?, ?, ?)",
        [
            ("newer@example.com", "newer@example.com", "@collide_me  ", "2026-09-17T11:00:00"),
            ("older@example.com", "older@example.com", "Collide_Me", "2026-09-17T10:00:00"),
            ("solo@example.com", "solo@example.com", "Solo_GG", "2026-09-17T12:00:00"),
            ("quiet@example.com", "quiet@example.com", None, "2026-09-17T13:00:00"),
        ],
    )
    conn.commit()
    conn.close()


def test_the_migration_keeps_the_oldest_holder_of_a_collision(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.delenv("JOIN_CODE", raising=False)
    legacy_db(tmp_path)

    assert db.init_db() == {"filled": 2, "cleared": 1}

    assert stored("older@example.com") == ("Collide_Me", "collide_me")
    assert stored("newer@example.com") == (None, None), "the newer row kept a name it cannot hold"
    assert stored("solo@example.com") == ("Solo_GG", "solo_gg")
    assert stored("quiet@example.com") == (None, None)
    assert rows(None, "SELECT COUNT(*) AS n FROM entries")[0]["n"] == 4, "the migration lost a row"


def test_the_migration_is_idempotent_and_leaves_the_index_in_place(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.delenv("JOIN_CODE", raising=False)
    legacy_db(tmp_path)
    db.init_db()

    assert db.init_db() == {"filled": 0, "cleared": 0}
    assert stored("older@example.com") == ("Collide_Me", "collide_me")

    conn = db.connect()
    try:
        with pytest.raises(sqlite3.IntegrityError):
            insert_entry(conn, "third@example.com", "COLLIDE_ME", "collide_me")
    finally:
        conn.close()


def test_the_migrated_loser_still_claims_a_link_and_can_take_a_free_name(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.delenv("JOIN_CODE", raising=False)
    legacy_db(tmp_path)
    db.init_db()

    result = db.claim("newer@example.com", "collide_me")
    assert result["link_url"] == "https://runpod.io/live"
    assert result["username_refused"] is True
    assert stored("newer@example.com") == (None, None)

    second = db.claim("newer@example.com", "their_own_name")
    assert second["link_url"] == "https://runpod.io/live", "the link changed"
    assert second["username_refused"] is False
    assert stored("newer@example.com") == ("their_own_name", "their_own_name")


def test_the_refused_person_is_not_given_contradictory_advice(client, auth):
    """A refusal must not also claim we have no username, nor say starting over fixes it.

    Both were true of the first wording and they contradict the refusal sentence
    printed directly underneath, which starting over does not fix.
    """
    load(client, auth)
    submit(client, "holder@example.com", "shared_name")

    # The case that matters: they typed a code as well, so the verdict block renders.
    client.cookies.clear()
    client.post("/email", data={"join_code": "anything", "discord_username": "@Shared_Name"})
    copy = visible_text(client.post("/claim", data={"email": "refused@example.com"}).text)

    assert REFUSED in copy
    assert "We do not have your Discord username" not in copy
    assert "Start over to fix it" not in copy
