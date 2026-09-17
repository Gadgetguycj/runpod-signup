import threading

from app import db

from .conftest import rows

LINKS = 10
CLAIMERS = 24


def test_concurrent_claims_hand_out_each_link_once(data_dir):
    db.import_links("\n".join(f"https://runpod.io/credit/{n}" for n in range(LINKS)))

    gate = threading.Barrier(CLAIMERS)
    results: list[dict] = []
    failures: list[BaseException] = []
    lock = threading.Lock()

    def worker(index: int) -> None:
        gate.wait()
        try:
            outcome = db.claim(f"racer{index}@example.com", None)
        except BaseException as exc:  # noqa: BLE001
            with lock:
                failures.append(exc)
            return
        with lock:
            results.append(outcome)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(CLAIMERS)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert failures == [], f"claims raised: {failures!r}"
    assert len(results) == CLAIMERS

    handed_out = [r["link_url"] for r in results if r["link_url"] is not None]
    assert len(handed_out) == LINKS, f"expected {LINKS} links handed out, got {len(handed_out)}"
    assert len(set(handed_out)) == LINKS, "a link was handed out more than once"

    claimed = rows(data_dir, "SELECT id, claimed_by_entry_id FROM links"
                             " WHERE claimed_by_entry_id IS NOT NULL")
    owners = [row["claimed_by_entry_id"] for row in claimed]
    assert len(claimed) == LINKS
    assert len(set(owners)) == LINKS, "one entry ended up owning two links"

    per_entry = rows(data_dir, "SELECT link_id, COUNT(*) AS n FROM entries"
                              " WHERE link_id IS NOT NULL GROUP BY link_id HAVING n > 1")
    assert per_entry == [], "two entries point at the same link"

    counts = db.stats()
    assert counts["entries"] == CLAIMERS
    assert counts["links_claimed"] == LINKS
    assert counts["links_remaining"] == 0


def test_concurrent_claims_by_the_same_person_yield_one_link(data_dir):
    db.import_links("\n".join(f"https://runpod.io/credit/{n}" for n in range(LINKS)))

    gate = threading.Barrier(CLAIMERS)
    results: list[dict] = []
    failures: list[BaseException] = []
    lock = threading.Lock()
    aliases = ["same.person@gmail.com", "sameperson+tag@googlemail.com", "SamePerson@gmail.com"]

    def worker(index: int) -> None:
        gate.wait()
        try:
            outcome = db.claim(aliases[index % len(aliases)], None)
        except BaseException as exc:  # noqa: BLE001
            with lock:
                failures.append(exc)
            return
        with lock:
            results.append(outcome)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(CLAIMERS)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert failures == [], f"claims raised: {failures!r}"
    urls = {r["link_url"] for r in results}
    assert len(urls) == 1, f"the same person was handed {len(urls)} different links: {urls}"
    assert db.stats()["entries"] == 1
    assert db.stats()["links_claimed"] == 1
