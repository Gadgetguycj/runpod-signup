"""Correcting a mistyped Discord username.

The username is the raffle entry, so redoing the form has to fix it. A later
non-empty username replaces the stored one. A later empty one leaves it alone,
so re-submitting only to see the code again never costs someone their entry.
"""

import csv
import io
import re

from app import db

from .conftest import visible_text, walk

LINK = re.compile(r'class="linkbox" href="([^"]+)"')


def load(client, auth, count=5):
    body = "\n".join(f"https://runpod.io/redeem/{n}" for n in range(count))
    assert client.post("/admin/links", headers=auth, content=body).status_code == 200


def resubmit(client, email, discord=""):
    """Redo the form from the start, the way an attendee correcting a typo would."""
    client.cookies.clear()
    return walk(client, email, discord)


def stored_username(email):
    return db.find_entry(db.normalize_email(email))["discord_username"]


def export_row(client, auth, email):
    export = client.get("/admin/entries.csv", headers=auth).text
    rows = [r for r in csv.DictReader(io.StringIO(export)) if r["raw_email"] == email]
    assert len(rows) == 1
    return rows[0]


def link_count(client, auth):
    stats = client.get("/admin/stats", headers=auth).json()
    return stats["links_claimed"]


EMAIL = "typo.fixer@example.com"


def test_a_later_username_replaces_the_stored_one(client, auth):
    load(client, auth)
    first = resubmit(client, EMAIL, "wrongname")
    assert stored_username(EMAIL) == "wrongname"
    claimed = link_count(client, auth)

    second = resubmit(client, EMAIL, "rightname")
    assert stored_username(EMAIL) == "rightname"
    assert export_row(client, auth, EMAIL)["discord_username"] == "rightname"
    assert export_row(client, auth, EMAIL)["in_raffle"] == "yes"

    assert LINK.search(first.text).group(1) == LINK.search(second.text).group(1)
    assert link_count(client, auth) == claimed


def test_a_blank_resubmit_does_not_wipe_the_stored_username(client, auth):
    load(client, auth)
    first = resubmit(client, EMAIL, "keepme")
    claimed = link_count(client, auth)

    second = resubmit(client, EMAIL)
    assert stored_username(EMAIL) == "keepme", "a blank re-submit wiped the raffle entry"
    assert export_row(client, auth, EMAIL)["discord_username"] == "keepme"
    assert export_row(client, auth, EMAIL)["in_raffle"] == "yes"
    # The session now holds a blank username while the entry holds "keepme", so the
    # page has to render the stored one or a blank re-submit looks like a lost entry.
    assert "keepme" in visible_text(second.text), "the page read the session, not the entry"

    assert LINK.search(first.text).group(1) == LINK.search(second.text).group(1)
    assert link_count(client, auth) == claimed


def test_a_username_added_later_enters_a_blank_entry_in_the_raffle(client, auth):
    load(client, auth)
    first = resubmit(client, EMAIL)
    assert stored_username(EMAIL) is None
    assert export_row(client, auth, EMAIL)["in_raffle"] == "no"
    claimed = link_count(client, auth)

    second = resubmit(client, EMAIL, "latecomer")
    assert stored_username(EMAIL) == "latecomer"
    assert export_row(client, auth, EMAIL)["discord_username"] == "latecomer"
    assert export_row(client, auth, EMAIL)["in_raffle"] == "yes"

    assert LINK.search(first.text).group(1) == LINK.search(second.text).group(1)
    assert link_count(client, auth) == claimed


def test_the_result_page_shows_the_corrected_username(client, auth):
    """That line is how a person checks their entry took, so it must not lag."""
    load(client, auth)
    resubmit(client, EMAIL, "wrongname")
    corrected = resubmit(client, EMAIL, "rightname")
    copy = visible_text(corrected.text)
    assert "rightname" in copy
    assert "wrongname" not in copy
    assert "Your Discord username rightname is in the raffle for RunPod swag." in copy


def test_the_empty_pool_page_shows_the_corrected_username(client):
    resubmit(client, EMAIL, "wrongname")
    corrected = resubmit(client, EMAIL, "rightname")
    copy = visible_text(corrected.text)
    assert "You are on the list" in copy
    assert "rightname" in copy
    assert "wrongname" not in copy


def test_correcting_a_username_never_hands_out_a_second_link(client, auth):
    load(client, auth)
    seen = set()
    for name in ["one", "two", "", "three", "", "four"]:
        response = resubmit(client, EMAIL, name)
        seen.add(LINK.search(response.text).group(1))
    assert len(seen) == 1, f"correcting the username handed out {len(seen)} links"
    assert link_count(client, auth) == 1
    assert client.get("/admin/stats", headers=auth).json()["entries"] == 1
    assert stored_username(EMAIL) == "four"


def test_the_correction_is_case_and_space_tolerant(client, auth):
    """Both storage paths, the insert for a new entry and the update for a correction."""
    load(client, auth)
    resubmit(client, EMAIL, "  Spaced_Name  ")
    assert stored_username(EMAIL) == "Spaced_Name", "the insert path did not trim"
    resubmit(client, EMAIL, "  Corrected_Name  ")
    assert stored_username(EMAIL) == "Corrected_Name", "the update path did not trim"


def test_a_blank_first_submit_leaves_the_entry_out_of_the_raffle(client, auth):
    load(client, auth)
    resubmit(client, EMAIL)
    stats = client.get("/admin/stats", headers=auth).json()
    assert stats["raffle_entries"] == 0
    assert stats["entries_without_discord"] == 1
    assert "raffle" not in visible_text(resubmit(client, EMAIL).text).lower()



def test_db_claim_trims_the_username_it_stores(data_dir):
    """Pinned at the database boundary, on the insert path and the update path."""
    db.import_links("https://runpod.io/redeem/0")
    db.claim("trim.me@example.com", "  Spaced_Name  ")
    assert db.find_entry("trim.me@example.com")["discord_username"] == "Spaced_Name"
    db.claim("trim.me@example.com", "  Corrected_Name  ")
    assert db.find_entry("trim.me@example.com")["discord_username"] == "Corrected_Name"


def test_db_claim_overwrites_only_with_a_non_empty_username(data_dir):
    db.import_links("https://runpod.io/redeem/0")
    db.claim("rules@example.com", "first")
    assert db.find_entry("rules@example.com")["discord_username"] == "first"

    db.claim("rules@example.com", "second")
    assert db.find_entry("rules@example.com")["discord_username"] == "second"

    for blank in ("", "   ", None):
        db.claim("rules@example.com", blank)
        assert db.find_entry("rules@example.com")["discord_username"] == "second", blank
