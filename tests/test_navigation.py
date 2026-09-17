"""Browser back, reload and typed URLs. Nobody may ever see raw JSON."""

import base64
import pathlib
import re

import pytest

from .conftest import start_session, walk


def load(client, auth, count=2):
    body = "\n".join(f"https://runpod.io/redeem/{n}" for n in range(count))
    assert client.post("/admin/links", headers=auth, content=body).status_code == 200


@pytest.mark.parametrize("path", ["/email", "/claim"])
def test_typed_url_without_a_session_redirects_home(client, path):
    response = client.get(path)
    assert response.status_code == 200
    assert str(response.url).endswith("/")
    assert "Join the raffle" in response.text
    assert "Method Not Allowed" not in response.text
    assert "detail" not in response.text


@pytest.mark.parametrize("path", ["/email", "/claim"])
def test_typed_url_without_a_session_never_returns_json(client, path):
    response = client.get(path, follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/"


def test_get_email_renders_step_two_for_a_visitor_with_a_session(client):
    client.post("/email", data={"discord_username": "revisit_me"})
    response = client.get("/email")
    assert response.status_code == 200
    assert "Where should the credit go?" in response.text
    assert "revisit_me" in response.text


def test_get_claim_re_renders_the_visitors_own_link(client, auth):
    load(client, auth)
    first = walk(client, "reloader@example.com")
    assert "https://runpod.io/redeem/0" in first.text

    reload = client.get("/claim")
    assert reload.status_code == 200
    assert "https://runpod.io/redeem/0" in reload.text
    assert "reloader@example.com" in reload.text
    assert client.get("/admin/stats", headers=auth).json()["links_claimed"] == 1


def test_get_claim_re_renders_the_empty_pool_page(client):
    walk(client, "onthelist@example.com")
    again = client.get("/claim")
    assert "You are on the list" in again.text
    assert "onthelist@example.com" in again.text


def test_submitting_redirects_so_a_reload_does_not_repost(client, auth):
    load(client, auth)
    start_session(client)
    posted = client.post("/claim", data={"email": "prg@example.com"}, follow_redirects=False)
    assert posted.status_code == 303
    assert posted.headers["location"] == "/claim"


def test_email_step_redirects_so_a_reload_does_not_repost(client):
    posted = client.post("/email", data={"discord_username": "x"}, follow_redirects=False)
    assert posted.status_code == 303
    assert posted.headers["location"] == "/email"


def test_a_validation_error_lands_on_the_email_step_url(client):
    """The URL must not say /claim while the page shows step two."""
    response = walk(client, "not-an-email")
    assert str(response.url).endswith("/email")
    assert "does not look like an email" in response.text
    assert "not-an-email" in response.text


def test_a_validation_error_clears_once_it_has_been_shown(client):
    walk(client, "not-an-email")
    assert "does not look like an email" not in client.get("/email").text


def test_unknown_url_gives_a_branded_page_not_json(client):
    response = client.get("/does-not-exist")
    assert response.status_code == 404
    assert "text/html" in response.headers["content-type"]
    assert "Page not found" in response.text
    assert "Coffee and Code AI Agent Hackathon" in response.text
    assert '{"detail"' not in response.text


def test_a_wrong_method_gives_a_branded_page_not_json(client):
    response = client.post("/health")
    assert response.status_code == 404
    assert "text/html" in response.headers["content-type"]
    assert "Method Not Allowed" not in response.text


def test_a_browser_hitting_admin_gets_a_branded_page(client):
    response = client.get("/admin/stats", headers={"Accept": "text/html,application/xhtml+xml"})
    assert response.status_code == 401
    assert "text/html" in response.headers["content-type"]
    assert '{"detail"' not in response.text


def test_a_tool_hitting_admin_still_gets_json(client):
    response = client.get("/admin/stats", headers={"Accept": "*/*"})
    assert response.status_code == 401
    assert response.json() == {"detail": "Send a valid bearer token."}


def test_flow_pages_are_not_cached(client):
    for response in [client.get("/"), client.post("/email", data={"discord_username": ""})]:
        assert response.headers["cache-control"] == "no-store"


def test_the_discord_username_survives_the_back_link(client):
    """Back is a plain link home, so the session has to carry the username."""
    client.post("/email", data={"discord_username": "sticky_name"})
    assert 'value="sticky_name"' in client.get("/").text


def test_security_headers_are_present(client):
    headers = client.get("/").headers
    assert headers["x-content-type-options"] == "nosniff"
    assert headers["referrer-policy"] == "strict-origin-when-cross-origin"
    assert headers["x-frame-options"] == "DENY"
    assert "frame-ancestors 'none'" in headers["content-security-policy"]
    assert "script-src 'self'" in headers["content-security-policy"]


def test_a_forged_session_cookie_cannot_read_someone_elses_link(client, auth):
    """The attack: name a victim's address in an unsigned cookie and read their credit."""
    load(client, auth)
    victim = walk(client, "victim@example.com")
    assert "https://runpod.io/redeem/0" in victim.text

    client.cookies.clear()
    forged = base64.urlsafe_b64encode(b'{"e":"victim@example.com"}').decode().rstrip("=")
    client.cookies.set("rp_session", f"{forged}.ZGVhZGJlZWY")
    response = client.get("/claim")
    assert "https://runpod.io/redeem/0" not in response.text, "a forged cookie read a link"
    assert str(response.url).endswith("/")


def test_a_cookie_signed_with_the_wrong_key_is_ignored(client, auth, monkeypatch):
    load(client, auth)
    walk(client, "victim@example.com")
    monkeypatch.setenv("SECRET_KEY", "an-entirely-different-key")
    assert "runpod.io/redeem/0" not in client.get("/claim").text


def test_over_long_inputs_are_refused_by_the_server(client):
    response = walk(client, "a" * 250 + "@example.com")
    assert "longer than 254 characters" in response.text
    response = client.post("/email", data={"discord_username": "z" * 300})
    assert "z" * 65 not in response.text


def test_assets_are_content_hashed_so_a_redeploy_busts_the_cache(client):
    """A cached stylesheet on a phone must not survive a redeploy."""
    page = client.get("/").text
    match = re.search(r'href="(/static/style\.css\?v=([0-9a-f]{10}))"', page)
    assert match, "the stylesheet URL carries no content hash"
    assert client.get(match.group(1)).status_code == 200

    from app.main import STATIC_DIR, asset

    stylesheet = pathlib.Path(STATIC_DIR) / "style.css"
    original = stylesheet.read_bytes()
    before = asset("style.css")
    try:
        stylesheet.write_bytes(original + b"\n/* changed */\n")
        assert asset("style.css") != before, "the hash did not change when the file did"
    finally:
        stylesheet.write_bytes(original)
    assert asset("style.css") == before


def test_the_script_urls_are_hashed_too(client, auth):
    load(client, auth)
    assert re.search(r'src="/static/form\.js\?v=[0-9a-f]{10}"', client.get("/").text)
    body = walk(client, "scripts@example.com").text
    assert re.search(r'src="/static/copy\.js\?v=[0-9a-f]{10}"', body)
