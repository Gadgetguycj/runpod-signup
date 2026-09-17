"""The words an attendee reads. These are the blockers the live inspection filed."""

import re

from .conftest import visible_text, walk

BANNED_ON_THE_EMPTY_POOL_PAGE = [
    "email",  # the app has no mail path, so it must never promise one
    "taken",  # the pool is empty because links are not loaded, not because of a race
    "gone",
    "ran out",
    "sold out",
    "all claimed",
]


def empty_pool_page(client) -> str:
    """No links have been imported, so a submit lands on the empty pool page."""
    response = walk(client, "hopeful@example.com")
    assert response.status_code == 200
    assert "You are on the list" in response.text
    return response.text


def body_copy(html: str) -> str:
    """Just the words a reader sees, lowercased."""
    return re.sub(r"\s+", " ", visible_text(html)).lower()


def test_empty_pool_page_never_promises_an_email(client):
    """The app cannot send mail. Saying it will is a false promise to every attendee."""
    copy = body_copy(empty_pool_page(client))
    for word in BANNED_ON_THE_EMPTY_POOL_PAGE:
        assert word not in copy, f"the empty pool page says {word!r}"


def test_empty_pool_page_echoes_the_address_back(client):
    assert "hopeful@example.com" in visible_text(empty_pool_page(client))


def test_empty_pool_page_points_at_the_table_for_a_link(client):
    copy = body_copy(empty_pool_page(client))
    assert "not ready yet" in copy
    assert "galaxygate table" in copy


def test_empty_pool_page_keeps_the_heading(client):
    assert "<h1>You are on the list</h1>" in empty_pool_page(client)


def test_result_page_echoes_the_address_back(client, auth):
    client.post("/admin/links", headers=auth, content="https://runpod.io/redeem/1")
    body = walk(client, "Typo.Check@example.com").text
    assert "Typo.Check@example.com" in visible_text(body)
    assert "start over" in body


def test_step_two_heading_is_a_question(client):
    body = client.post("/email", data={"discord_username": ""}).text
    assert "Where should the credit go?" in body


def test_no_page_mentions_email_delivery(client, auth):
    client.post("/admin/links", headers=auth, content="https://runpod.io/redeem/1")
    for body in [
        walk(client, "got.a.link@example.com").text,
        walk(client, "no.link.left@example.com").text,
    ]:
        copy = body_copy(body)
        assert "emailed" not in copy
        assert "we will email" not in copy
        assert "check your inbox" not in copy
