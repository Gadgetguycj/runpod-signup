"""Chase's rules for page one and for what the raffle actually is.

The raffle entry is the Discord username. The email only pins a credit link to a
person. No page shows the remaining work, and no page promises to send anything.
"""

import csv
import io
import re

import pytest

from .conftest import start_session, visible_text, walk

# Words that would state or imply something gets sent to the visitor.
SENDING = [
    "emailed", "e-mailed", "we will email", "we'll email", "sent to you", "we will send",
    "we'll send", "check your inbox", "in your inbox", "watch your inbox", "mailed to you",
    "you will receive", "you'll receive", "will be sent", "look out for",
]

# Anything that outlines the remaining work.
PROGRESS_MARKERS = [
    'class="progress"', 'aria-label="Progress"', 'aria-current="step"',
    "step 1", "step 2", "step 3", "step one", "step two", "of 2", "of 3",
    "1 discord", "2 email", "3 credit",
]


def load(client, auth, count=2):
    body = "\n".join(f"https://runpod.io/redeem/{n}" for n in range(count))
    assert client.post("/admin/links", headers=auth, content=body).status_code == 200


def every_page(client, auth):
    """One of each page a visitor can reach, including the error paths."""
    load(client, auth)
    pages = {}
    pages["page one"] = client.get("/").text
    pages["page one restart"] = client.get("/?restart=1").text
    start_session(client, "chase_gg")
    pages["page two"] = client.get("/email").text
    pages["page two error"] = client.post("/claim", data={"email": "nope"}).text
    pages["result"] = walk(client, "winner@example.com", "chase_gg").text
    client.cookies.clear()
    pages["result no discord"] = walk(client, "nodiscord@example.com").text
    client.cookies.clear()
    pages["empty pool"] = walk(client, "toolate@example.com", "latecomer").text
    client.cookies.clear()
    pages["empty pool no discord"] = walk(client, "toolate2@example.com").text
    client.cookies.clear()
    client.post("/admin/allowed-emails", headers=auth, content="onlythis@example.com")
    pages["not allowed"] = walk(client, "stranger@example.com").text
    pages["not found"] = client.get("/no-such-page").text
    pages["admin in a browser"] = client.get(
        "/admin/stats", headers={"Accept": "text/html"}
    ).text
    return pages


# ---------- 1. the step indicator is gone ----------


def test_no_page_shows_a_step_indicator(client, auth):
    for name, html in every_page(client, auth).items():
        lowered = html.lower()
        for marker in PROGRESS_MARKERS:
            assert marker not in lowered, f"{name} still outlines the remaining work: {marker}"


def test_the_stylesheet_carries_no_progress_rules(client):
    assert ".progress" not in client.get("/static/style.css").text


# ---------- 2. the heading ----------


def test_page_one_heading_is_join_the_raffle_with_a_quiet_qualifier(client):
    html = client.get("/").text
    heading = re.search(r"<h1>(.*?)</h1>", html, re.S).group(1)
    assert "Join the Raffle" in heading
    assert '<span class="qualifier">(Optional)</span>' in heading
    assert re.sub(r"\s+", " ", visible_text(heading)).strip() == "Join the Raffle (Optional)"


def test_the_qualifier_is_smaller_and_lighter_than_the_heading(client):
    css = client.get("/static/style.css").text
    block = re.search(r"\.qualifier \{(.*?)\}", css, re.S).group(1)
    assert "font-size: 0.6em" in block
    assert "--quiet" in block
    assert "--quiet: #939cae;" in css


def test_the_qualifier_grey_meets_small_text_contrast(client):
    def luminance(value):
        value = value.lstrip("#")
        def channel(pair):
            c = int(pair, 16) / 255
            return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4
        return (0.2126 * channel(value[0:2]) + 0.7152 * channel(value[2:4])
                + 0.0722 * channel(value[4:6]))

    light, dark = luminance("#939cae"), luminance("#151922")
    assert (max(light, dark) + 0.05) / (min(light, dark) + 0.05) >= 4.5


# ---------- 3. no invitation to skip ----------


def test_page_one_does_not_tell_anyone_they_can_skip(client):
    copy = visible_text(client.get("/").text).lower()
    for phrase in ["skip", "you can still", "optional.", "if you prefer", "leave it blank",
                   "you do not have to", "not required", "no need to"]:
        assert phrase not in copy, f"page one still hints at skipping: {phrase}"


def test_optionality_is_stated_once_and_only_on_the_heading(client):
    copy = visible_text(client.get("/").text).lower()
    assert copy.count("optional") == 1


# ---------- 4. the raffle entry is the Discord username and the channel code ----------


def test_page_one_says_the_code_and_the_username_are_the_raffle_entry(client):
    """Both halves. A username alone is no longer an entry."""
    html = client.get("/").text
    assert "<label for=\"discord_username\">Discord username</label>" in html
    assert '<label for="join_code">Code from the Discord channel</label>' in html
    helper = re.search(r'<p class="help">(.*?)</p>', html, re.S).group(1).strip()
    assert helper == "The code and the username together enter you in the raffle."
    assert helper.count(".") == 1, "the helper must be one short line"


def sentences(html: str) -> list[str]:
    copy = re.sub(r"\s+", " ", visible_text(html))
    return [s.strip().lower() for s in re.split(r"(?<=[.!?])\s+", copy) if s.strip()]


def test_no_sentence_ties_the_email_to_the_raffle(client, auth):
    """The raffle entry is the Discord username. The email only pins a credit link."""
    for name, html in every_page(client, auth).items():
        for sentence in sentences(html):
            if "raffle" in sentence:
                assert "email" not in sentence, f"{name}: {sentence!r}"
                assert "address" not in sentence, f"{name}: {sentence!r}"


def test_no_page_uses_the_old_raffle_spot_wording(client, auth):
    for name, html in every_page(client, auth).items():
        copy = re.sub(r"\s+", " ", visible_text(html)).lower()
        for phrase in ["email gets you", "email enters", "raffle spot",
                       "spot in the raffle", "and your raffle"]:
            assert phrase not in copy, f"{name} ties the email to the raffle: {phrase}"


def test_the_sentence_scan_would_catch_a_real_claim():
    """Guards the guard."""
    bad = "Your email gets you the RunPod credit link and your raffle spot."
    found = [s for s in [bad.lower()] if "raffle" in s and "email" in s]
    assert found


def test_the_result_page_names_the_discord_username_as_the_raffle_entry(client, auth):
    load(client, auth)
    copy = visible_text(walk(client, "winner@example.com", "chase_gg").text)
    assert "chase_gg" in copy
    assert "is in the raffle for RunPod swag" in copy


def test_the_result_page_claims_no_raffle_entry_without_a_username(client, auth):
    load(client, auth)
    copy = visible_text(walk(client, "nodiscord@example.com").text).lower()
    assert "raffle" not in copy, "a raffle entry was claimed with no Discord username"
    assert "runpod.io/redeem/0" in copy


def test_the_empty_pool_page_says_the_username_is_in_the_raffle(client):
    copy = visible_text(walk(client, "toolate@example.com", "latecomer").text)
    assert "You are on the list" in copy
    assert "latecomer" in copy
    assert "is in the raffle for RunPod swag" in copy


def test_the_empty_pool_page_claims_no_raffle_entry_without_a_username(client):
    html = walk(client, "toolate@example.com").text
    copy = visible_text(html)
    assert "raffle" not in copy.lower(), "a raffle spot was claimed with no Discord username"
    assert "recorded" not in copy.lower()
    assert "Your $15 credit link is not ready yet." in copy
    assert "toolate@example.com" in copy


# ---------- 4. drawing the raffle from the export ----------


def test_the_csv_marks_who_is_in_the_raffle(client, auth):
    load(client, auth)
    walk(client, "entered@example.com", "raffle_name")
    client.cookies.clear()
    walk(client, "notentered@example.com")

    export = client.get("/admin/entries.csv", headers=auth).text
    rows = list(csv.DictReader(io.StringIO(export)))
    assert [r["discord_username"] for r in rows] == ["raffle_name", ""]
    assert [r["in_raffle"] for r in rows] == ["yes", "no"]

    drawable = [r["discord_username"] for r in rows if r["in_raffle"] == "yes"]
    assert drawable == ["raffle_name"]


def test_an_entry_with_no_username_is_distinguishable_in_the_csv(client, auth):
    load(client, auth)
    walk(client, "blank@example.com")
    row = list(csv.DictReader(io.StringIO(client.get("/admin/entries.csv", headers=auth).text)))[0]
    assert row["discord_username"] == ""
    assert row["in_raffle"] == "no"


def test_stats_counts_the_raffle_separately_from_the_entries(client, auth):
    load(client, auth)
    walk(client, "a@example.com", "one")
    client.cookies.clear()
    walk(client, "b@example.com", "two")
    client.cookies.clear()
    walk(client, "c@example.com")

    stats = client.get("/admin/stats", headers=auth).json()
    assert stats["entries"] == 3
    assert stats["raffle_entries"] == 2
    assert stats["entries_without_discord"] == 1


# ---------- 5. nothing promises to send anything ----------


@pytest.mark.parametrize("phrase", SENDING)
def test_no_page_promises_to_send_anything(client, auth, phrase):
    for name, html in every_page(client, auth).items():
        copy = re.sub(r"\s+", " ", visible_text(html)).lower()
        assert phrase not in copy, f"{name} promises to send something: {phrase}"


def test_the_sending_scan_would_catch_a_real_promise():
    """Guards the guard. A list that matches nothing would pass every test above."""
    sample = "your link will be emailed to you, so check your inbox"
    assert [p for p in SENDING if p in sample]


def test_resubmitting_the_same_email_shows_the_same_code(client, auth):
    load(client, auth)
    first = walk(client, "Pinned.Person+conf@gmail.com", "pinned")
    assert "https://runpod.io/redeem/0" in first.text
    client.cookies.clear()
    again = walk(client, "pinnedperson@googlemail.com")
    assert "https://runpod.io/redeem/0" in again.text
    assert client.get("/admin/stats", headers=auth).json()["links_claimed"] == 1
