"""The address must survive Cloudflare Email Address Obfuscation with no JavaScript.

Cloudflare rewrites any text node in the served HTML that matches an email address
into the literal "[email protected]" plus a decoder script. On the empty pool page
that would replace the one line telling an attendee their entry took. These tests
run the same scan Cloudflare does over the rendered markup and assert nothing in it
would be rewritten, while the visible text still reads as the real address.
"""

import re

import pytest

from .conftest import text_nodes, visible_text, walk

# The pattern Cloudflare's obfuscator looks for in a text node.
CLOUDFLARE_EMAIL = r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"

ADDRESSES = [
    "second.person@example.com",
    "a.b+tag@gmail.com",
    "UPPER.Case@Example.CO.UK",
    "x@y.dev",
    "very.long.name.with.dots+conference2026@subdomain.example.org",
]


def cloudflare_would_rewrite(html: str) -> list[str]:
    """Every text node Cloudflare's obfuscator would replace."""
    return [node for node in text_nodes(html) if re.search(CLOUDFLARE_EMAIL, node)]


def empty_pool(client, address):
    response = walk(client, address)
    assert "You are on the list" in response.text
    return response.text


def with_link(client, auth, address):
    client.post("/admin/links", headers=auth, content="https://runpod.io/redeem/1")
    response = walk(client, address)
    assert 'class="linkbox"' in response.text
    return response.text


def blocked(client, auth, address):
    client.post("/admin/allowed-emails", headers=auth, content="someone.else@example.com")
    response = walk(client, address)
    assert "do not have that address" in response.text
    return response.text


@pytest.mark.parametrize("address", ADDRESSES)
def test_empty_pool_page_has_no_text_node_cloudflare_would_rewrite(client, address):
    html = empty_pool(client, address)
    assert cloudflare_would_rewrite(html) == []


@pytest.mark.parametrize("address", ADDRESSES)
def test_result_page_has_no_text_node_cloudflare_would_rewrite(client, auth, address):
    html = with_link(client, auth, address)
    assert cloudflare_would_rewrite(html) == []


def test_not_allowed_page_has_no_text_node_cloudflare_would_rewrite(client, auth):
    html = blocked(client, auth, "turned.away@example.com")
    assert cloudflare_would_rewrite(html) == []


@pytest.mark.parametrize("address", ADDRESSES)
def test_empty_pool_page_still_reads_as_the_real_address(client, address):
    """No JavaScript anywhere in this assertion. It is the served bytes."""
    assert address in visible_text(empty_pool(client, address))


@pytest.mark.parametrize("address", ADDRESSES)
def test_result_page_still_reads_as_the_real_address(client, auth, address):
    assert address in visible_text(with_link(client, auth, address))


def test_not_allowed_page_still_reads_as_the_real_address(client, auth):
    address = "turned.away@example.com"
    assert address in visible_text(blocked(client, auth, address))


def test_the_address_is_split_across_three_adjacent_nodes(client):
    """Adjacent with no whitespace, so a copy yields the exact string."""
    html = empty_pool(client, "split.me@example.com")
    nodes = text_nodes(html)
    assert "split.me" in nodes
    assert "@" in nodes
    assert "example.com" in nodes
    at = nodes.index("@")
    assert nodes[at - 1] == "split.me"
    assert nodes[at + 1] == "example.com"
    assert "".join(nodes[at - 1 : at + 2]) == "split.me@example.com"


def test_no_whitespace_leaks_into_the_rendered_address(client):
    html = empty_pool(client, "tight@example.com")
    assert "We have you as tight@example.com" in visible_text(html)


def test_an_address_with_no_at_sign_still_renders(client):
    """Defensive. The validator rejects these, so the macro must not crash on one."""
    from app.main import templates

    rendered = str(templates.env.get_template("_address.html").module.address("no-at-sign"))
    assert visible_text(rendered) == "no-at-sign", rendered
    assert "@" not in rendered, "the macro invented an at sign that was not in the input"


def test_the_pattern_used_here_really_does_match_a_plain_address():
    """Guards the guard. A pattern that matches nothing would pass every test above."""
    for address in ADDRESSES:
        assert re.search(CLOUDFLARE_EMAIL, address), address
    assert cloudflare_would_rewrite("<p>plain@example.com</p>") == ["plain@example.com"]
