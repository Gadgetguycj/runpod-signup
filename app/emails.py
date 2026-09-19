"""Normalization used for the one per person rules and for the visit log."""

import ipaddress

GMAIL_DOMAINS = {"gmail.com", "googlemail.com"}


def normalize_email(raw: str) -> str:
    """Collapse an address to the identity used by the unique index.

    Trim, lowercase, drop everything from the first plus to the at sign in the
    local part. For gmail.com and googlemail.com also drop dots from the local
    part and fold both domains to gmail.com.
    """
    value = (raw or "").strip().lower()
    if "@" not in value:
        return value
    local, _, domain = value.rpartition("@")
    local = local.split("+", 1)[0]
    if domain in GMAIL_DOMAINS:
        local = local.replace(".", "")
        domain = "gmail.com"
    return f"{local}@{domain}"


def normalize_discord_username(raw: str) -> str:
    """Collapse a Discord username to the identity the unique index sits on.

    Trim, drop a single leading at sign, lowercase. Nothing else. A Discord name is not
    an email address, so folding dots or underscores here would merge two real people.
    """
    value = (raw or "").strip()
    if value.startswith("@"):
        value = value[1:]
    return value.lower()


def truncate_ip(ip: str | None) -> str | None:
    """Zero the last octet of an IPv4 address, keep the /64 of an IPv6 one."""
    if not ip:
        return None
    try:
        address = ipaddress.ip_address(ip.strip().split("%", 1)[0])
    except ValueError:
        return None
    if address.version == 4:
        return str(ipaddress.IPv4Address(address.packed[:3] + b"\x00"))
    return f"{ipaddress.IPv6Address(address.packed[:8] + bytes(8))}/64"
