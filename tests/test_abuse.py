"""Abuse protection. The pool is about 200 links worth roughly $3,000."""

import threading

import pytest

from app import db

from .conftest import rows, walk

ROOM_IP = "198.51.100.0"
ATTACKER_IP = "203.0.113.0"


def load(client, auth, count=50):
    body = "\n".join(f"https://runpod.io/redeem/{n}" for n in range(count))
    assert client.post("/admin/links", headers=auth, content=body).status_code == 200


def claim(client, email, ip=ROOM_IP):
    """One attendee walking the form. A fresh cookie jar, one phone per person."""
    client.cookies.clear()
    return walk(client, email, headers={"Cf-Connecting-Ip": ip})


def got_a_link(response) -> bool:
    return 'class="linkbox"' in response.text


def test_a_scripted_loop_of_unique_addresses_is_stopped(client, auth, monkeypatch):
    """The attack the pool has to survive: one host, a fresh address every request."""
    monkeypatch.setenv("CLAIM_RATE_LIMIT_PER_HOUR", "5")
    load(client, auth, count=50)

    handed_out = sum(got_a_link(claim(client, f"bot{n}@throwaway.test", ATTACKER_IP))
                     for n in range(40))
    assert handed_out == 5, f"the loop drained {handed_out} links, the cap is 5"
    assert db.stats()["links_remaining"] == 45


def test_the_stopped_loop_still_sees_the_on_the_list_page(client, auth, monkeypatch):
    """Nobody is shown an error, so a real attendee caught by the limit is fine."""
    monkeypatch.setenv("CLAIM_RATE_LIMIT_PER_HOUR", "1")
    load(client, auth)
    claim(client, "first@example.com", ATTACKER_IP)
    blocked = claim(client, "second@example.com", ATTACKER_IP)
    assert blocked.status_code == 200
    assert "You are on the list" in blocked.text
    assert not got_a_link(blocked)


def test_every_blocked_attempt_is_still_recorded_for_the_raffle(client, auth, monkeypatch):
    monkeypatch.setenv("CLAIM_RATE_LIMIT_PER_HOUR", "2")
    load(client, auth)
    for n in range(10):
        claim(client, f"person{n}@example.com", ATTACKER_IP)
    assert db.stats()["entries"] == 10
    assert db.stats()["links_claimed"] == 2


def test_a_normal_attendee_is_never_rate_limited(client, auth):
    """One person, one address, taps submit, reloads, comes back later."""
    load(client, auth)
    first = claim(client, "attendee@example.com")
    assert got_a_link(first)
    for _ in range(20):
        again = client.post("/claim", data={"email": "attendee@example.com"},
                            headers={"Cf-Connecting-Ip": ROOM_IP})  # same phone, same session
        assert got_a_link(again)
        assert client.get("/claim").text.count("runpod.io/redeem/0") >= 1
    assert db.stats()["links_claimed"] == 1
    assert rows(None, "SELECT COUNT(*) AS n FROM claim_events")[0]["n"] == 1, (
        "a returning attendee must not burn rate limit quota"
    )


def test_a_room_behind_one_venue_nat_is_not_locked_out(client, auth):
    """Fifty phones on one wifi share a truncated address. The default must cover it."""
    load(client, auth, count=60)
    handed_out = sum(got_a_link(claim(client, f"guest{n}@example.com", ROOM_IP))
                     for n in range(50))
    assert handed_out == 50, f"only {handed_out} of 50 people in the room got a link"


def test_the_limit_is_per_address_not_global(client, auth, monkeypatch):
    monkeypatch.setenv("CLAIM_RATE_LIMIT_PER_HOUR", "2")
    load(client, auth)
    for n in range(5):
        claim(client, f"a{n}@example.com", "203.0.113.0")
    assert got_a_link(claim(client, "elsewhere@example.com", "198.51.100.0"))
    assert db.stats()["links_claimed"] == 3


def test_the_global_breaker_stops_a_distributed_drain(client, auth, monkeypatch):
    """Many source addresses, so the per address limit never fires."""
    monkeypatch.setenv("CLAIM_RATE_LIMIT_PER_HOUR", "100")
    monkeypatch.setenv("GLOBAL_CLAIM_LIMIT_PER_HOUR", "4")
    load(client, auth, count=50)
    handed_out = sum(got_a_link(claim(client, f"bot{n}@throwaway.test", f"203.0.{n}.0"))
                     for n in range(30))
    assert handed_out == 4, f"the breaker let {handed_out} links go, the cap is 4"
    assert db.stats()["entries"] == 30


def test_the_breaker_shows_the_on_the_list_page(client, auth, monkeypatch):
    monkeypatch.setenv("GLOBAL_CLAIM_LIMIT_PER_HOUR", "1")
    load(client, auth)
    claim(client, "lucky@example.com", "203.0.113.0")
    tripped = claim(client, "unlucky@example.com", "198.51.100.0")
    assert "You are on the list" in tripped.text
    assert not got_a_link(tripped)


def test_the_breaker_is_logged_loudly(client, auth, monkeypatch, caplog):
    monkeypatch.setenv("GLOBAL_CLAIM_LIMIT_PER_HOUR", "1")
    load(client, auth)
    claim(client, "lucky@example.com", "203.0.113.0")
    with caplog.at_level("WARNING", logger="runpod_signup"):
        claim(client, "unlucky@example.com", "198.51.100.0")
    assert any("CIRCUIT BREAKER TRIPPED" in record.getMessage()
               for record in caplog.records)


def test_the_rate_limit_is_logged(client, auth, monkeypatch, caplog):
    monkeypatch.setenv("CLAIM_RATE_LIMIT_PER_HOUR", "1")
    load(client, auth)
    claim(client, "one@example.com", ATTACKER_IP)
    with caplog.at_level("WARNING", logger="runpod_signup"):
        claim(client, "two@example.com", ATTACKER_IP)
    assert any("Rate limit hit" in record.getMessage() for record in caplog.records)


def test_claims_closed_records_the_entry_and_hands_out_nothing(client, auth, monkeypatch):
    load(client, auth)
    monkeypatch.setenv("CLAIMS_OPEN", "closed")
    paused = claim(client, "paused@example.com")
    assert paused.status_code == 200
    assert "You are on the list" in paused.text
    assert not got_a_link(paused)
    assert db.stats()["entries"] == 1
    assert db.stats()["links_claimed"] == 0
    assert db.stats()["links_remaining"] == 50


def test_claims_reopen_and_hand_out_again(client, auth, monkeypatch):
    load(client, auth)
    monkeypatch.setenv("CLAIMS_OPEN", "closed")
    claim(client, "paused@example.com")
    monkeypatch.setenv("CLAIMS_OPEN", "open")
    assert got_a_link(claim(client, "paused@example.com"))
    assert db.stats()["links_claimed"] == 1


def test_claims_are_open_by_default(client, auth, monkeypatch):
    monkeypatch.delenv("CLAIMS_OPEN", raising=False)
    load(client, auth)
    assert got_a_link(claim(client, "default@example.com"))


@pytest.mark.parametrize("value", ["0", "false", "no", "off", "CLOSED"])
def test_claims_open_accepts_the_obvious_ways_to_say_no(client, auth, monkeypatch, value):
    load(client, auth)
    monkeypatch.setenv("CLAIMS_OPEN", value)
    assert not got_a_link(claim(client, "paused@example.com"))


def test_admin_stats_exposes_the_limits_and_the_breaker(client, auth, monkeypatch):
    monkeypatch.setenv("CLAIM_RATE_LIMIT_PER_HOUR", "7")
    monkeypatch.setenv("GLOBAL_CLAIM_LIMIT_PER_HOUR", "2")
    load(client, auth)
    claim(client, "one@example.com", ATTACKER_IP)
    claim(client, "two@example.com", ATTACKER_IP)

    handout = client.get("/admin/stats", headers=auth).json()["handout"]
    assert handout["claims_open"] is True
    assert handout["per_address_limit_per_hour"] == 7
    assert handout["global_limit_per_hour"] == 2
    assert handout["claimed_last_hour"] == 2
    assert handout["breaker_tripped"] is True
    assert handout["busiest_addresses_last_hour"] == [{"ip_key": ATTACKER_IP, "claims": 2}]


def test_admin_stats_shows_claims_closed(client, auth, monkeypatch):
    monkeypatch.setenv("CLAIMS_OPEN", "closed")
    assert client.get("/admin/stats", headers=auth).json()["handout"]["claims_open"] is False


def test_a_burst_of_concurrent_claims_cannot_beat_the_limit(data_dir):
    """The check and the handout share one transaction, so a race cannot slip past."""
    db.import_links("\n".join(f"https://runpod.io/redeem/{n}" for n in range(60)))
    attempts = 40
    gate = threading.Barrier(attempts)
    results: list[dict] = []
    failures: list[BaseException] = []
    lock = threading.Lock()

    def worker(index: int) -> None:
        gate.wait()
        try:
            outcome = db.claim(f"bot{index}@throwaway.test", None, ip_key=ATTACKER_IP,
                               per_ip_limit=5, global_limit=1000, claims_open=True)
        except BaseException as exc:  # noqa: BLE001
            with lock:
                failures.append(exc)
            return
        with lock:
            results.append(outcome)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(attempts)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert failures == [], f"claims raised: {failures!r}"
    handed_out = [r["link_url"] for r in results if r["link_url"]]
    assert len(handed_out) == 5, f"{len(handed_out)} links escaped a cap of 5"
    assert len(set(handed_out)) == 5
    assert db.stats()["entries"] == attempts


def test_a_bare_request_loop_with_no_session_gets_nothing(client, auth):
    """The cheap drain: POST the claim endpoint directly, a new address every time."""
    load(client, auth)
    for n in range(200):
        client.cookies.clear()
        response = client.post("/claim", data={"email": f"loop{n}@throwaway.test"},
                               headers={"Cf-Connecting-Ip": ATTACKER_IP})
        assert not got_a_link(response)
    assert db.stats()["links_claimed"] == 0
    assert db.stats()["entries"] == 0
    assert db.stats()["links_remaining"] == 50


def test_a_bare_loop_is_sent_back_to_the_start_with_an_explanation(client, auth):
    load(client, auth)
    client.cookies.clear()
    response = client.post("/claim", data={"email": "loop@throwaway.test"})
    assert response.status_code == 200
    assert "Please start here" in response.text
    assert "cookies" in response.text


def test_the_default_limits_let_a_full_room_through_on_one_nat(client, auth, monkeypatch):
    """The stated requirement: one venue NAT must not lock the room out of the pool."""
    for name in ("CLAIM_RATE_LIMIT_PER_HOUR", "GLOBAL_CLAIM_LIMIT_PER_HOUR", "CLAIMS_OPEN"):
        monkeypatch.delenv(name, raising=False)
    load(client, auth, count=130)
    handed_out = sum(got_a_link(claim(client, f"guest{n}@example.com", ROOM_IP))
                     for n in range(120))
    assert handed_out == 120, f"only {handed_out} of a 120 person room got a link"


def test_the_per_address_limit_binds_before_the_site_wide_cap(client, auth, monkeypatch):
    """A single source must hit its own limit first, or the site wide cap is meaningless."""
    for name in ("CLAIM_RATE_LIMIT_PER_HOUR", "GLOBAL_CLAIM_LIMIT_PER_HOUR"):
        monkeypatch.delenv(name, raising=False)
    from app import config

    assert config.claim_rate_limit_per_hour() < config.global_claim_limit_per_hour()


def test_a_returning_attendee_is_served_even_when_the_limit_is_exhausted(client, auth, monkeypatch):
    """The limit gates new links leaving the pool. It never refuses someone their own."""
    monkeypatch.setenv("CLAIM_RATE_LIMIT_PER_HOUR", "1")
    load(client, auth)
    first = claim(client, "early.bird@example.com", ROOM_IP)
    assert got_a_link(first)

    assert not got_a_link(claim(client, "too.late@example.com", ROOM_IP))

    for _ in range(5):
        again = claim(client, "early.bird@example.com", ROOM_IP)
        assert got_a_link(again), "a returning attendee was refused their own link"
    assert rows(None, "SELECT COUNT(*) AS n FROM claim_events")[0]["n"] == 1
