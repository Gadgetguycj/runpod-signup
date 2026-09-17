from .conftest import rows


def test_page_one_view_is_recorded(client, data_dir):
    response = client.get("/", headers={
        "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X)",
        "Referer": "https://qr.example/event",
        "X-Forwarded-For": "203.0.113.45, 10.0.0.1",
    })
    assert response.status_code == 200

    visits = rows(data_dir, "SELECT * FROM visits")
    assert len(visits) == 1
    assert visits[0]["user_agent"].startswith("Mozilla/5.0 (iPhone")
    assert visits[0]["referrer"] == "https://qr.example/event"
    assert visits[0]["ip_truncated"] == "203.0.113.0"
    assert visits[0]["created_at"]


def test_every_page_one_view_counts(client, data_dir):
    for _ in range(4):
        client.get("/")
    assert rows(data_dir, "SELECT COUNT(*) AS n FROM visits")[0]["n"] == 4


def test_ipv6_visitor_is_recorded_as_a_64(client, data_dir):
    client.get("/", headers={"X-Forwarded-For": "2001:db8:1:2:3:4:5:6"})
    assert rows(data_dir, "SELECT ip_truncated FROM visits")[0]["ip_truncated"] == "2001:db8:1:2::/64"


def test_later_pages_do_not_add_visits(client, data_dir):
    client.get("/")
    client.post("/email", data={"discord_username": "x"})
    client.post("/claim", data={"email": "a@b.example"})
    assert rows(data_dir, "SELECT COUNT(*) AS n FROM visits")[0]["n"] == 1


# Header shapes below are the ones captured live from https://runpod.galaxygate.app,
# which sits behind Cloudflare and then the GalaxyGate proxy. Cf-Connecting-Ip is the
# visitor. X-Real-Ip is the Cloudflare edge and must never be recorded.
CLOUDFLARE_HEADERS = {
    "Cf-Connecting-Ip": "144.172.70.17",
    "X-Forwarded-For": "144.172.70.17, 104.23.190.72",
    "X-Real-Ip": "104.23.190.72",
    "X-Forwarded-Proto": "https",
    "Cf-Ipcountry": "US",
}


def test_cloudflare_header_wins_over_the_others(client, data_dir):
    client.get("/", headers=CLOUDFLARE_HEADERS)
    visit = rows(data_dir, "SELECT ip_truncated, country FROM visits")[0]
    assert visit["ip_truncated"] == "144.172.70.0"
    assert visit["country"] == "US"


def test_edge_address_is_never_recorded(client, data_dir):
    client.get("/", headers=CLOUDFLARE_HEADERS)
    stored = rows(data_dir, "SELECT ip_truncated FROM visits")[0]["ip_truncated"]
    assert stored != "104.23.190.0", "the Cloudflare edge address was recorded as the visitor"


def test_falls_back_to_forwarded_for_without_the_cloudflare_header(client, data_dir):
    headers = {k: v for k, v in CLOUDFLARE_HEADERS.items() if k != "Cf-Connecting-Ip"}
    client.get("/", headers=headers)
    assert rows(data_dir, "SELECT ip_truncated FROM visits")[0]["ip_truncated"] == "144.172.70.0"


def test_falls_back_to_the_socket_peer_without_any_proxy_header(client, data_dir):
    client.get("/")
    visit = rows(data_dir, "SELECT ip_truncated, country FROM visits")[0]
    assert visit["ip_truncated"] is None  # the test transport peer is not an IP address
    assert visit["country"] is None


def test_cloudflare_ipv6_visitor_keeps_the_64(client, data_dir):
    client.get("/", headers={"Cf-Connecting-Ip": "2606:4700:4700::1111",
                             "X-Forwarded-For": "198.51.100.9"})
    assert rows(data_dir, "SELECT ip_truncated FROM visits")[0]["ip_truncated"] == "2606:4700:4700::/64"
