import csv
import io

from .conftest import ADMIN_TOKEN, walk

ADMIN_GETS = ["/admin/stats", "/admin/entries.csv"]
ADMIN_POSTS = ["/admin/links", "/admin/allowed-emails"]


def test_admin_without_a_token_is_401(client):
    for path in ADMIN_GETS:
        assert client.get(path).status_code == 401, path
    for path in ADMIN_POSTS:
        assert client.post(path, content="https://runpod.io/credit/x").status_code == 401, path


def test_admin_with_a_wrong_token_is_401(client):
    headers = {"Authorization": "Bearer nope"}
    assert client.get("/admin/stats", headers=headers).status_code == 401
    assert client.get("/admin/stats", headers={"Authorization": ADMIN_TOKEN}).status_code == 401


def test_admin_is_503_when_admin_token_is_unset(client_no_admin):
    for path in ADMIN_GETS:
        response = client_no_admin.get(path, headers={"Authorization": f"Bearer {ADMIN_TOKEN}"})
        assert response.status_code == 503, path
        assert "ADMIN_TOKEN" in response.text
    for path in ADMIN_POSTS:
        assert client_no_admin.post(path, content="x").status_code == 503, path
    assert client_no_admin.get("/health").json() == {"status": "ok"}


def test_link_import_counts_added_and_skipped(client, auth):
    first = client.post("/admin/links", headers=auth,
                        content="https://a.example/1\n\n  \nhttps://a.example/2\nhttps://a.example/1\n")
    assert first.json() == {"added": 2, "skipped": 1}
    second = client.post("/admin/links", headers=auth, content="https://a.example/2\nhttps://a.example/3")
    assert second.json() == {"added": 1, "skipped": 1}
    assert client.get("/admin/stats", headers=auth).json()["links_total"] == 3


def test_allowed_email_import_normalizes(client, auth):
    result = client.post("/admin/allowed-emails", headers=auth,
                         content="A.B+tag@gmail.com\nab@googlemail.com\n\nc@d.example\n")
    assert result.json() == {"added": 2, "skipped": 1}


def test_stats_and_csv_export(client, auth):
    client.post("/admin/links", headers=auth, content="https://a.example/1\nhttps://a.example/2")
    client.get("/")
    walk(client, "Draw.Me+x@gmail.com", "drawme")

    stats = client.get("/admin/stats", headers=auth).json()
    assert {k: v for k, v in stats.items() if k != "handout"} == {
        "visits": 1, "entries": 1, "links_total": 2, "links_claimed": 1, "links_remaining": 1}

    export = client.get("/admin/entries.csv", headers=auth)
    assert export.headers["content-type"].startswith("text/csv")
    table = list(csv.reader(io.StringIO(export.text)))
    assert table[0] == ["raw_email", "normalized_email", "discord_username", "claimed_link",
                        "created_at", "link_claimed_at"]
    assert table[1][:4] == ["Draw.Me+x@gmail.com", "drawme@gmail.com", "drawme",
                            "https://a.example/1"]
    assert table[1][4] and table[1][5]


def test_import_accepts_a_raw_body_labelled_as_form_encoded(client, auth):
    """curl --data-binary sends form encoding by default. The list must still land."""
    body = "https://runpod.io/redeem/aaaa1111\nhttps://runpod.io/redeem/bbbb2222\n"
    response = client.post(
        "/admin/links", headers={**auth, "Content-Type": "application/x-www-form-urlencoded"},
        content=body,
    )
    assert response.json() == {"added": 2, "skipped": 0}
    assert client.get("/admin/stats", headers=auth).json()["links_total"] == 2


def test_import_accepts_an_items_form_field(client, auth):
    response = client.post("/admin/links", headers=auth,
                           data={"items": "https://runpod.io/redeem/x\nhttps://runpod.io/redeem/y"})
    assert response.json() == {"added": 2, "skipped": 0}


def test_import_accepts_text_plain(client, auth):
    response = client.post("/admin/links", headers={**auth, "Content-Type": "text/plain"},
                           content="https://runpod.io/redeem/z\n")
    assert response.json() == {"added": 1, "skipped": 0}
