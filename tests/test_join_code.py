"""The channel code and the raffle.

A code is posted in a RunPod Discord channel. It gates the raffle and nothing else.
Nobody is ever blocked: every visitor reaches the email step and a credit link whatever
they type. To be in the raffle a person needs both a matching code and a Discord
username. Either half alone is not an entry.
"""

import csv
import io
import logging
import re
import sqlite3

import pytest
from fastapi.testclient import TestClient

from app import config, db
from app.main import app

from .conftest import ADMIN_TOKEN, visible_text

CODE = "Hackathon-7Q"
LINK = re.compile(r'class="linkbox" href="([^"]+)"')


def arm(monkeypatch, code=CODE):
    """Set the expected code. The conftest clears it, so the check is off by default."""
    monkeypatch.setenv("JOIN_CODE", code)


def load(client, auth, count=3):
    body = "\n".join(f"https://runpod.io/redeem/{n}" for n in range(count))
    assert client.post("/admin/links", headers=auth, content=body).status_code == 200


def page_one(client, code="", discord=""):
    return client.post("/email", data={"join_code": code, "discord_username": discord})


def walk(client, email, code="", discord=""):
    """Page one to the result, the way an attendee goes."""
    client.cookies.clear()
    page_one(client, code, discord)
    return client.post("/claim", data={"email": email})


def stats(client, auth):
    return client.get("/admin/stats", headers=auth).json()


def export_row(client, auth, email):
    export = client.get("/admin/entries.csv", headers=auth).text
    rows = [r for r in csv.DictReader(io.StringIO(export)) if r["raw_email"] == email]
    assert len(rows) == 1
    return rows[0]


# ---------- nobody is ever blocked ----------


@pytest.mark.parametrize("typed", ["", "not-the-code", CODE, "   ", "x" * 64])
def test_next_always_advances_whatever_is_typed(client, monkeypatch, typed):
    arm(monkeypatch)
    response = page_one(client, typed, "cj_gg")
    assert response.status_code == 200
    assert str(response.url).endswith("/email")
    assert "Where should the credit go?" in response.text


def test_a_wrong_code_still_gets_a_credit_link(client, auth, monkeypatch):
    arm(monkeypatch)
    load(client, auth)
    result = walk(client, "wrongcode@example.com", "not-the-code", "cj_gg")
    assert LINK.search(result.text).group(1) == "https://runpod.io/redeem/0"


def test_page_one_never_shows_a_rejection(client, monkeypatch):
    arm(monkeypatch)
    for typed in ["", "not-the-code", CODE]:
        page_one(client, typed, "cj_gg")
        for where, html in [("page one", client.get("/").text),
                            ("the email step", client.get("/email").text)]:
            copy = visible_text(html).lower()
            for word in ["not right", "does not match", "wrong", "invalid", "try again"]:
                assert word not in copy, f"{where} rejected {typed!r}: {word}"


# ---------- the four raffle combinations ----------


def test_a_matching_code_and_a_username_is_in_the_raffle(client, auth, monkeypatch):
    arm(monkeypatch)
    load(client, auth)
    copy = visible_text(walk(client, "both@example.com", CODE, "winner_gg").text)
    assert "Your Discord username winner_gg is in the raffle for RunPod swag." in copy
    assert "not in the raffle" not in copy
    assert export_row(client, auth, "both@example.com")["in_raffle"] == "yes"
    assert stats(client, auth)["raffle_entries"] == 1


def test_a_matching_code_with_no_username_is_not_in_the_raffle(client, auth, monkeypatch):
    arm(monkeypatch)
    load(client, auth)
    copy = visible_text(walk(client, "codeonly@example.com", CODE).text)
    assert "You are not in the raffle." in copy
    assert "We do not have your Discord username." in copy
    assert "does not match" not in copy
    assert export_row(client, auth, "codeonly@example.com")["in_raffle"] == "no"
    assert stats(client, auth)["raffle_entries"] == 0


def test_a_wrong_code_with_a_username_is_not_in_the_raffle(client, auth, monkeypatch):
    arm(monkeypatch)
    load(client, auth)
    copy = visible_text(walk(client, "badcode@example.com", "not-the-code", "hopeful").text)
    assert "You are not in the raffle." in copy
    assert "That code does not match the one in the Discord channel." in copy
    assert "We do not have your Discord username." not in copy
    assert export_row(client, auth, "badcode@example.com")["in_raffle"] == "no"
    assert stats(client, auth)["raffle_entries"] == 0


def test_a_username_with_no_code_is_not_in_the_raffle(client, auth, monkeypatch):
    arm(monkeypatch)
    load(client, auth)
    copy = visible_text(walk(client, "nameonly@example.com", "", "hopeful").text)
    assert "You are not in the raffle." in copy
    assert "We do not have the code from the Discord channel." in copy
    assert export_row(client, auth, "nameonly@example.com")["in_raffle"] == "no"
    assert stats(client, auth)["raffle_entries"] == 0


def test_neither_half_says_nothing_about_the_raffle(client, auth, monkeypatch):
    arm(monkeypatch)
    load(client, auth)
    copy = visible_text(walk(client, "quiet@example.com").text)
    assert "raffle" not in copy.lower()
    assert export_row(client, auth, "quiet@example.com")["in_raffle"] == "no"
    assert stats(client, auth)["raffle_entries"] == 0


def test_the_empty_pool_page_carries_the_same_verdict(client, monkeypatch):
    """No links are loaded, so this is the other page that reports the raffle."""
    arm(monkeypatch)
    good = visible_text(walk(client, "pool.in@example.com", CODE, "winner_gg").text)
    assert "You are on the list" in good
    assert "Your Discord username winner_gg is in the raffle for RunPod swag." in good

    bad = visible_text(walk(client, "pool.out@example.com", "nope", "hopeful").text)
    assert "You are on the list" in bad
    assert "You are not in the raffle." in bad
    assert "That code does not match the one in the Discord channel." in bad


# ---------- what the code check forgives ----------


@pytest.mark.parametrize(
    "typed", ["  Hackathon-7Q", "Hackathon-7Q   ", "\tHackathon-7Q\n", " Hackathon-7Q "]
)
def test_the_comparison_trims_surrounding_whitespace(monkeypatch, typed):
    """Pinned on the comparison, which is where the rule is written down."""
    arm(monkeypatch)
    assert config.join_code_accepted(typed) is True


@pytest.mark.parametrize("typed", ["hackathon-7q", "HACKATHON-7Q", "hAcKaThOn-7q"])
def test_the_comparison_ignores_case(monkeypatch, typed):
    arm(monkeypatch)
    assert config.join_code_accepted(typed) is True


def test_the_comparison_matches_the_code_as_configured(monkeypatch):
    arm(monkeypatch)
    assert config.join_code_accepted(CODE) is True


@pytest.mark.parametrize("typed", ["Hackathon 7Q", "Hackathon7Q", "Hackathon-7Q!", ""])
def test_the_comparison_forgives_nothing_else(monkeypatch, typed):
    arm(monkeypatch)
    assert config.join_code_accepted(typed) is False


@pytest.mark.parametrize(
    "typed", ["  Hackathon-7Q", "Hackathon-7Q   ", "\tHackathon-7Q\n", "  Hackathon-7Q  "]
)
def test_surrounding_whitespace_is_forgiven_end_to_end(client, monkeypatch, typed):
    """People retype this from a phone."""
    arm(monkeypatch)
    copy = visible_text(walk(client, "typist@example.com", typed, "typist_gg").text)
    assert "is in the raffle for RunPod swag." in copy


@pytest.mark.parametrize("typed", ["hackathon-7q", "HACKATHON-7Q", "  hAcKaThOn-7q  "])
def test_case_is_forgiven_end_to_end(client, monkeypatch, typed):
    arm(monkeypatch)
    copy = visible_text(walk(client, "shouty@example.com", typed, "shouty_gg").text)
    assert "is in the raffle for RunPod swag." in copy


@pytest.mark.parametrize(
    "typed",
    ["Hackathon 7Q", "Hackathon7Q", "Hack athon-7Q", "Hackathon-7Q!", "Hackathon_7Q"],
)
def test_nothing_beyond_trim_and_case_is_forgiven(client, monkeypatch, typed):
    arm(monkeypatch)
    copy = visible_text(walk(client, "close@example.com", typed, "close_gg").text)
    assert "You are not in the raffle." in copy


def test_a_code_configured_with_stray_whitespace_still_matches(client, monkeypatch):
    """An env file that ends the value with a newline must not cost everybody the raffle."""
    arm(monkeypatch, "  Hackathon-7Q\n")
    copy = visible_text(walk(client, "envtypo@example.com", CODE, "envtypo_gg").text)
    assert "is in the raffle for RunPod swag." in copy


# ---------- fixing it on a later submit ----------


def test_a_later_code_and_username_flip_an_entry_into_the_raffle(client, auth, monkeypatch):
    arm(monkeypatch)
    load(client, auth)
    first = walk(client, "fixer@example.com", "wrong-code", "fixer_gg")
    assert "You are not in the raffle." in visible_text(first.text)
    assert export_row(client, auth, "fixer@example.com")["in_raffle"] == "no"

    second = walk(client, "fixer@example.com", CODE, "fixer_gg")
    assert "Your Discord username fixer_gg is in the raffle for RunPod swag." in visible_text(
        second.text
    )
    assert export_row(client, auth, "fixer@example.com")["in_raffle"] == "yes"
    assert stats(client, auth)["raffle_entries"] == 1

    assert LINK.search(first.text).group(1) == LINK.search(second.text).group(1)
    assert stats(client, auth)["links_claimed"] == 1
    assert stats(client, auth)["entries"] == 1


def test_a_blank_code_does_not_undo_a_stored_one(client, auth, monkeypatch):
    arm(monkeypatch)
    load(client, auth)
    first = walk(client, "keeper@example.com", CODE, "keeper_gg")
    assert export_row(client, auth, "keeper@example.com")["in_raffle"] == "yes"

    second = walk(client, "keeper@example.com")
    copy = visible_text(second.text)
    assert "Your Discord username keeper_gg is in the raffle for RunPod swag." in copy
    assert export_row(client, auth, "keeper@example.com")["in_raffle"] == "yes"

    assert LINK.search(first.text).group(1) == LINK.search(second.text).group(1)
    assert stats(client, auth)["links_claimed"] == 1


def test_a_run_of_resubmits_never_hands_out_a_second_link(client, auth, monkeypatch):
    arm(monkeypatch)
    load(client, auth)
    seen = set()
    for code, discord in [("wrong", ""), ("", "late_gg"), (CODE, ""), ("", ""), (CODE, "late_gg")]:
        seen.add(LINK.search(walk(client, "loop@example.com", code, discord).text).group(1))
    assert len(seen) == 1, f"re-submitting handed out {len(seen)} links"
    assert stats(client, auth)["links_claimed"] == 1
    assert export_row(client, auth, "loop@example.com")["in_raffle"] == "yes"


def test_db_claim_overwrites_the_code_only_with_a_non_empty_one(data_dir):
    """Pinned at the database boundary, the same rule the username follows."""
    db.import_links("https://runpod.io/redeem/0")
    db.claim("rules@example.com", "name", join_code="first")
    assert db.find_entry("rules@example.com")["join_code"] == "first"

    db.claim("rules@example.com", "name", join_code="  second  ")
    assert db.find_entry("rules@example.com")["join_code"] == "second"

    for blank in ("", "   ", None):
        db.claim("rules@example.com", "name", join_code=blank)
        assert db.find_entry("rules@example.com")["join_code"] == "second", blank


def test_the_column_is_added_to_a_database_written_before_this_change(tmp_path, monkeypatch):
    """The site is live with rows, so the new column has to arrive by migration."""
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    before = """
    CREATE TABLE links (id INTEGER PRIMARY KEY AUTOINCREMENT, url TEXT NOT NULL UNIQUE,
      claimed_by_entry_id INTEGER REFERENCES entries(id), claimed_at TEXT,
      created_at TEXT NOT NULL);
    CREATE TABLE entries (id INTEGER PRIMARY KEY AUTOINCREMENT, raw_email TEXT NOT NULL,
      normalized_email TEXT NOT NULL UNIQUE, discord_username TEXT,
      link_id INTEGER REFERENCES links(id), created_at TEXT NOT NULL, link_claimed_at TEXT);
    CREATE TABLE allowed_emails (normalized_email TEXT PRIMARY KEY);
    CREATE TABLE visits (id INTEGER PRIMARY KEY AUTOINCREMENT, created_at TEXT NOT NULL,
      user_agent TEXT, referrer TEXT, ip_truncated TEXT);
    INSERT INTO links (url, created_at) VALUES ('https://runpod.io/redeem/live', 'then');
    INSERT INTO entries (raw_email, normalized_email, discord_username, created_at)
      VALUES ('early@example.com', 'early@example.com', 'early_gg', 'then');
    """
    conn = sqlite3.connect(tmp_path / "signup.db")
    conn.executescript(before)
    conn.commit()
    conn.close()

    db.init_db()
    early = db.find_entry("early@example.com")
    assert early["discord_username"] == "early_gg", "the migration lost a row"
    assert early["join_code"] is None

    arm(monkeypatch)
    assert db.in_raffle(early) is False, "a row from before the change cannot be in the raffle"
    assert db.claim("early@example.com", "early_gg", join_code=CODE)["link_url"] == (
        "https://runpod.io/redeem/live"
    )
    assert db.in_raffle(db.find_entry("early@example.com")) is True


# ---------- the code never leaves the server ----------


def test_the_code_never_appears_in_a_page_or_an_admin_response(client, auth, monkeypatch):
    arm(monkeypatch)
    load(client, auth)
    page_one(client, CODE, "leaky_gg")
    bodies = {
        "page one": client.get("/").text,
        "email step": client.get("/email").text,
        "email error": client.post("/claim", data={"email": "nope"}).text,
        "result": client.post("/claim", data={"email": "leaky@example.com"}).text,
        "result reloaded": client.get("/claim").text,
        "empty pool": walk(client, "pool@example.com", CODE, "pool_gg").text,
        "not found": client.get("/no-such-page").text,
        "stats": client.get("/admin/stats", headers=auth).text,
        "health": client.get("/health", headers=auth).text,
        "entries csv": client.get("/admin/entries.csv", headers=auth).text,
    }
    for name, body in bodies.items():
        assert CODE.lower() not in body.lower(), f"{name} carries the code"


def test_the_error_and_the_field_give_nothing_away_about_the_code(client, auth, monkeypatch):
    arm(monkeypatch)
    load(client, auth)
    field = re.search(r'<input id="join_code".*?>', client.get("/").text, re.S).group(0)
    assert CODE.lower() not in field.lower()
    copy = visible_text(walk(client, "hint@example.com", "wrong", "hint_gg").text).lower()
    for hint in ["characters", "digits", "letters", "length", "uppercase", "lowercase",
                 "starts with", "begins with", "hyphen", "long"]:
        assert hint not in copy, f"the page hints at the shape of the code: {hint}"


# ---------- the check is off when JOIN_CODE is unset ----------


def test_with_no_join_code_a_username_alone_is_in_the_raffle(client, auth):
    """Failing open is deliberate. A config slip must not cost the room the raffle."""
    load(client, auth)
    copy = visible_text(walk(client, "open@example.com", "", "open_gg").text)
    assert "Your Discord username open_gg is in the raffle for RunPod swag." in copy
    assert export_row(client, auth, "open@example.com")["in_raffle"] == "yes"
    assert stats(client, auth)["raffle_entries"] == 1


def test_with_no_join_code_any_typed_code_is_accepted(client, auth):
    load(client, auth)
    copy = visible_text(walk(client, "anything@example.com", "whatever", "any_gg").text)
    assert "is in the raffle for RunPod swag." in copy
    assert export_row(client, auth, "anything@example.com")["in_raffle"] == "yes"


def test_with_no_join_code_a_missing_username_is_still_not_in_the_raffle(client, auth):
    load(client, auth)
    copy = visible_text(walk(client, "nameless@example.com", "whatever").text)
    assert "You are not in the raffle." in copy
    assert "We do not have your Discord username." in copy
    assert export_row(client, auth, "nameless@example.com")["in_raffle"] == "no"


def test_with_no_join_code_the_field_still_renders(client):
    html = client.get("/").text
    assert 'name="join_code"' in html
    assert "Code from the Discord channel" in html


def test_an_unset_join_code_warns_at_startup(data_dir, monkeypatch, caplog):
    monkeypatch.setenv("ADMIN_TOKEN", ADMIN_TOKEN)
    with caplog.at_level(logging.WARNING, logger="runpod_signup"):
        with TestClient(app):
            pass
    warnings = [r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING]
    assert any("JOIN_CODE is unset" in message for message in warnings), warnings


def test_an_armed_join_code_does_not_warn_at_startup(data_dir, monkeypatch, caplog):
    arm(monkeypatch)
    monkeypatch.setenv("ADMIN_TOKEN", ADMIN_TOKEN)
    with caplog.at_level(logging.WARNING, logger="runpod_signup"):
        with TestClient(app):
            pass
    assert "JOIN_CODE" not in caplog.text


# ---------- the organizer can see whether it is armed ----------


def test_stats_and_health_report_the_code_as_set(client, auth, monkeypatch):
    arm(monkeypatch)
    assert stats(client, auth)["join_code_set"] is True
    assert client.get("/health", headers=auth).json()["join_code_set"] is True
    assert "join_code_set" not in client.get("/health").json()


def test_stats_and_health_report_the_code_as_unset(client, auth):
    assert stats(client, auth)["join_code_set"] is False
    assert client.get("/health", headers=auth).json()["join_code_set"] is False


# ---------- field order ----------


def test_page_one_orders_the_button_then_the_code_then_the_username(client):
    html = client.get("/").text
    marks = [
        html.index("Open the RunPod Discord"),
        html.index('id="join_code"'),
        html.index('id="discord_username"'),
        html.index(">Next<"),
    ]
    assert marks == sorted(marks), "page one is out of order"
