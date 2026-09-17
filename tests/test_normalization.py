import pytest

from app.emails import normalize_email, truncate_ip


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("a.b+tag@gmail.com", "ab@gmail.com"),
        ("ab@googlemail.com", "ab@gmail.com"),
        ("A.B+Tag@GMAIL.COM", "ab@gmail.com"),
        ("  ab@gmail.com  ", "ab@gmail.com"),
        ("a.b@fastmail.com", "a.b@fastmail.com"),
        ("a.b+tag@fastmail.com", "a.b@fastmail.com"),
        ("CJ@GalaxyGate.NET", "cj@galaxygate.net"),
    ],
)
def test_alias_table(raw, expected):
    assert normalize_email(raw) == expected


def test_gmail_aliases_collapse_to_one_identity():
    assert normalize_email("a.b+tag@gmail.com") == normalize_email("ab@googlemail.com")


def test_non_gmail_keeps_its_dot():
    assert normalize_email("a.b@fastmail.com") != normalize_email("ab@fastmail.com")


@pytest.mark.parametrize(
    "ip, expected",
    [
        ("203.0.113.45", "203.0.113.0"),
        ("10.1.2.3", "10.1.2.0"),
        ("2001:db8:1:2:3:4:5:6", "2001:db8:1:2::/64"),
        ("not-an-ip", None),
        (None, None),
    ],
)
def test_ip_truncation(ip, expected):
    assert truncate_ip(ip) == expected
