import re

from app import db

from .conftest import rows, walk

LINK_RE = re.compile(r'class="linkbox" href="([^"]+)"')


def load_links(client, auth, count=3):
    body = "\n".join(f"https://runpod.io/credit/{n}" for n in range(1, count + 1))
    response = client.post("/admin/links", headers=auth, content=body)
    assert response.status_code == 200
    return response.json()


def submit(client, email, discord=""):
    """An attendee always arrives at the claim step through the form."""
    return walk(client, email, discord)


def test_three_pages_walk_through(client, auth):
    load_links(client, auth)

    page1 = client.get("/")
    assert page1.status_code == 200
    assert "https://discord.gg/testinvite" in page1.text
    assert 'action="/email"' in page1.text

    page2 = client.post("/email", data={"discord_username": "cj_gg"})
    assert page2.status_code == 200
    assert str(page2.url).endswith("/email")
    assert page2.request.method == "GET", "the form must redirect rather than render the POST"
    assert "cj_gg" in page2.text

    page3 = submit(client, "attendee@example.com", "cj_gg")
    assert page3.status_code == 200
    assert LINK_RE.search(page3.text).group(1) == "https://runpod.io/credit/1"


def test_discord_username_survives_to_the_entry(client, auth):
    load_links(client, auth)
    submit(client, "withdiscord@example.com", "cj_gg")
    stored = rows(None, "SELECT discord_username FROM entries WHERE normalized_email = ?",
                  ("withdiscord@example.com",))
    assert stored[0]["discord_username"] == "cj_gg"


def test_returning_visitor_gets_the_same_link(client, auth):
    load_links(client, auth)
    first = LINK_RE.search(submit(client, "Repeat.Person+conf@gmail.com").text).group(1)
    second = LINK_RE.search(submit(client, "repeatperson@googlemail.com").text).group(1)
    assert first == second
    assert db.stats()["links_claimed"] == 1
    assert db.stats()["entries"] == 1


def test_empty_pool_records_the_entry_without_a_link(client, auth):
    load_links(client, auth, count=1)
    assert LINK_RE.search(submit(client, "first@example.com").text) is not None

    overflow = submit(client, "second@example.com")
    assert overflow.status_code == 200
    assert "You are on the list" in overflow.text
    assert "GalaxyGate table" in overflow.text
    assert LINK_RE.search(overflow.text) is None
    assert "runpod.io/credit" not in overflow.text

    entry = rows(None, "SELECT link_id FROM entries WHERE normalized_email = ?",
                 ("second@example.com",))
    assert len(entry) == 1
    assert entry[0]["link_id"] is None
    assert db.stats()["links_remaining"] == 0


def test_no_invite_url_renders_a_disabled_button(client, monkeypatch):
    monkeypatch.delenv("DISCORD_INVITE_URL", raising=False)
    page = client.get("/")
    assert "Invite link coming soon" in page.text
    assert "disabled" in page.text
    assert "discord.gg" not in page.text


def test_bad_email_is_rejected_and_claims_nothing(client, auth):
    load_links(client, auth)
    response = submit(client, "not-an-email")
    assert response.status_code == 200
    assert "does not look like an email" in response.text
    assert db.stats()["entries"] == 0
    assert db.stats()["links_claimed"] == 0


def test_health_is_liveness_only_without_the_admin_token(client, auth):
    """A stranger must not learn how many credits are left."""
    load_links(client, auth, count=2)
    submit(client, "one@example.com")
    assert client.get("/health").json() == {"status": "ok"}
    assert client.get("/health", headers={"Authorization": "Bearer wrong"}).json() == {
        "status": "ok"
    }


def test_health_reports_pool_and_configuration_to_the_admin(client, auth):
    load_links(client, auth, count=2)
    submit(client, "one@example.com")
    assert client.get("/health", headers=auth).json() == {
        "status": "ok",
        "links_total": 2,
        "links_remaining": 1,
        "discord_invite_set": True,
        "admin_configured": True,
    }


def test_result_page_offers_the_link_and_a_copy_control(client, auth):
    load_links(client, auth)
    body = submit(client, "copyme@example.com").text
    assert 'class="linkbox" href="https://runpod.io/credit/1"' in body
    assert 'data-link="https://runpod.io/credit/1"' in body
    assert client.get("/static/copy.js").status_code == 200


def test_stylesheet_is_served_and_pulls_nothing_external(client):
    page = client.get("/").text
    assert '<link rel="stylesheet" href="/static/style.css?v=' in page
    assert "//fonts." not in page
    assert "cdn" not in page.lower()
    css = client.get("/static/style.css")  # the hash is a cache buster, not a path
    assert css.status_code == 200
    assert "@import" not in css.text
    assert "http://" not in css.text and "https://" not in css.text


def test_no_em_dashes_in_any_rendered_copy(client, auth):
    load_links(client, auth)
    pages = [
        client.get("/").text,
        client.post("/email", data={"discord_username": "cj"}).text,
        submit(client, "dash@example.com").text,
        submit(client, "bad").text,
    ]
    client.post("/admin/allowed-emails", headers=auth, content="only@example.com")
    pages.append(submit(client, "nope@example.com").text)
    for body in pages:
        assert "—" not in body
