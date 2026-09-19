"""Environment backed settings, read per request so they can be tuned live."""

import logging
import os

log = logging.getLogger("runpod_signup")

# A whole room behind one venue NAT is a single truncated address, so the per address
# limit is sized for a room and not for a person. It sits below the site wide cap so a
# single source binds first. The pool is about 200 links, so at least fifty survive any
# one hour of abuse. See the README for the full reasoning.
DEFAULT_CLAIM_RATE_LIMIT_PER_HOUR = 120
DEFAULT_GLOBAL_CLAIM_LIMIT_PER_HOUR = 150

TRUTHY = {"1", "true", "yes", "on", "open"}
FALSY = {"0", "false", "no", "off", "closed"}


def _positive_int(name: str, default: int) -> int:
    """A positive integer, or the default with a warning.

    A zero or a typo falls back rather than silently switching protection off or
    stopping every handout.
    """
    raw = (os.environ.get(name) or "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        log.warning("%s is %r, which is not a number. Using %d.", name, raw, default)
        return default
    if value <= 0:
        log.warning("%s is %d, which is not positive. Using %d.", name, value, default)
        return default
    return value


def claim_rate_limit_per_hour() -> int:
    return _positive_int("CLAIM_RATE_LIMIT_PER_HOUR", DEFAULT_CLAIM_RATE_LIMIT_PER_HOUR)


def global_claim_limit_per_hour() -> int:
    return _positive_int("GLOBAL_CLAIM_LIMIT_PER_HOUR", DEFAULT_GLOBAL_CLAIM_LIMIT_PER_HOUR)


def claims_open() -> bool:
    raw = (os.environ.get("CLAIMS_OPEN") or "").strip().lower()
    if not raw:
        return True
    if raw in TRUTHY:
        return True
    if raw in FALSY:
        return False
    log.warning("CLAIMS_OPEN is %r, which is not a yes or a no. Treating claims as open.", raw)
    return True


def join_code() -> str:
    """The code posted in the RunPod Discord channel. Empty means no gate."""
    return (os.environ.get("JOIN_CODE") or "").strip()


def join_code_set() -> bool:
    return bool(join_code())


def join_code_accepted(typed: str) -> bool:
    """Whether a typed code may pass page one.

    Surrounding whitespace and case are forgiven because people retype from a phone.
    Nothing else is normalized. An unset JOIN_CODE accepts anything on purpose, so a
    config slip at the event cannot stop every attendee getting a credit.
    """
    expected = join_code()
    if not expected:
        return True
    return (typed or "").strip().lower() == expected.lower()


def discord_invite_url() -> str:
    return (os.environ.get("DISCORD_INVITE_URL") or "").strip()


def discord_code_url() -> str:
    """Link to the Discord message carrying the join code.

    Only a member of the server can open it, which is the point: reading the
    code is the proof they joined.
    """
    return (os.environ.get("DISCORD_CODE_URL") or "").strip()


def admin_token() -> str:
    return (os.environ.get("ADMIN_TOKEN") or "").strip()
