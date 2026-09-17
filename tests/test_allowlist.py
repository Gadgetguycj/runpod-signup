import re

from app import db

from .conftest import walk

LINK_RE = re.compile(r'class="linkbox" href="([^"]+)"')


def load(client, auth, count=3):
    body = "\n".join(f"https://runpod.io/credit/{n}" for n in range(1, count + 1))
    assert client.post("/admin/links", headers=auth, content=body).status_code == 200


def test_empty_allowlist_allows_anybody(client, auth):
    load(client, auth)
    assert db.allowlist_count() == 0
    response = walk(client, "stranger@example.com")
    assert LINK_RE.search(response.text) is not None
    assert db.stats()["links_claimed"] == 1


def test_populated_allowlist_blocks_an_unknown_address(client, auth):
    load(client, auth)
    imported = client.post("/admin/allowed-emails", headers=auth,
                           content="Invited.Person+conf@gmail.com\n")
    assert imported.status_code == 200
    assert db.allowlist_count() == 1

    blocked = walk(client, "stranger@example.com")
    assert blocked.status_code == 200
    assert "do not have that address" in blocked.text
    assert LINK_RE.search(blocked.text) is None
    assert db.stats()["entries"] == 0
    assert db.stats()["links_claimed"] == 0


def test_populated_allowlist_admits_a_listed_alias(client, auth):
    load(client, auth)
    client.post("/admin/allowed-emails", headers=auth, content="Invited.Person+conf@gmail.com\n")
    allowed = walk(client, "invitedperson@googlemail.com")
    assert LINK_RE.search(allowed.text) is not None
    assert db.stats()["links_claimed"] == 1
