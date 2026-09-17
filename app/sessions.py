"""Signed cookie carrying the visitor through the three steps.

The value is base64url(json payload) then a dot then base64url(HMAC-SHA256).
The key comes from SECRET_KEY, or from a file in DATA_DIR that is created once
and kept, so a container restart does not invalidate everybody's session.
"""

import base64
import hmac
import json
import os
import secrets
from hashlib import sha256

from . import db

COOKIE_NAME = "rp_session"
COOKIE_MAX_AGE = 7 * 24 * 3600
MAX_COOKIE_BYTES = 3000


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _unb64(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def secret_key() -> bytes:
    """SECRET_KEY if it is set, otherwise a key stored once beside the database."""
    from_env = (os.environ.get("SECRET_KEY") or "").strip()
    if from_env:
        return from_env.encode()
    path = db.data_dir() / "secret.key"
    if path.exists():
        return path.read_bytes().strip()
    path.parent.mkdir(parents=True, exist_ok=True)
    key = secrets.token_hex(32).encode()
    path.write_bytes(key)
    os.chmod(path, 0o600)
    return key


def dump(payload: dict) -> str:
    body = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()
    mac = hmac.new(secret_key(), body, sha256).digest()
    return f"{_b64(body)}.{_b64(mac)}"


def load(cookie: str | None) -> dict:
    """Return the payload, or an empty dict when the cookie is absent or forged."""
    if not cookie or "." not in cookie or len(cookie) > MAX_COOKIE_BYTES:
        return {}
    encoded_body, _, encoded_mac = cookie.partition(".")
    try:
        body = _unb64(encoded_body)
        mac = _unb64(encoded_mac)
    except (ValueError, base64.binascii.Error):
        return {}
    expected = hmac.new(secret_key(), body, sha256).digest()
    if not hmac.compare_digest(mac, expected):
        return {}
    try:
        payload = json.loads(body)
    except ValueError:
        return {}
    return payload if isinstance(payload, dict) else {}
