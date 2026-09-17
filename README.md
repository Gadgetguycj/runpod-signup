# RunPod credit signup

Signup page for the Coffee and Code AI Agent Hackathon, served at
https://runpod.galaxygate.app. An attendee scans the QR code, optionally joins the
RunPod Discord, gives an email, and gets a single use $15 RunPod credit link. Every
entry is a row on the swag raffle list.

Python 3.12, FastAPI, Jinja2, SQLite from the standard library with WAL. Server
rendered, no build step, no client framework, no external fonts or CDNs.

## The flow

1. `GET /` Discord step. Invite button plus a Discord username field. The username is
   the raffle entry. The view is recorded in `visits`.
2. `POST /email` then `303` to `GET /email`. The email step. The Discord username is
   carried in a signed session cookie.
3. `POST /claim` then `303` to `GET /claim`. The result. Shows the personal credit link,
   or the "you are on the list" page, or the "we do not have that address" page when the
   allowlist is populated and does not contain the address.

Every form posts and then redirects, so a reload or a back-then-forward re-fetches the
page instead of re-posting. `GET /email` and `GET /claim` render from the session, so a
visitor who types the URL or comes back through history sees their own page. Without a
session they are redirected to `/`. No browser ever sees raw JSON. A 404 or a wrong
method renders the branded error page, and an admin route hit from a browser renders it
too, while a tool that does not ask for HTML still gets JSON.

## Environment variables

| Name | Default | Effect |
| --- | --- | --- |
| `DISCORD_INVITE_URL` | unset | The Discord button target. Unset renders the button disabled with the text "Invite link coming soon" and logs a warning at startup. |
| `ADMIN_TOKEN` | unset | Bearer token for every `/admin` route. Unset makes the whole admin surface return 503 and say why. |
| `DATA_DIR` | `/data` | Directory holding `signup.db` and `secret.key`. Mount a volume here. |
| `SECRET_KEY` | generated | Signs the session cookie. Unset means one is generated on first start and kept in `DATA_DIR/secret.key`, so a restart does not log everybody out. |
| `CLAIM_RATE_LIMIT_PER_HOUR` | `120` | Most links one truncated client address may take per hour. |
| `GLOBAL_CLAIM_LIMIT_PER_HOUR` | `150` | Most links the whole site may hand out per hour. |
| `CLAIMS_OPEN` | `open` | Set to `closed` to pause handouts while still recording entries. |

A non-positive or unparseable number falls back to the default and logs a warning, so a
typo cannot switch protection off or stop every handout.

Copy `.env.example` to `.env` for a local run.

## One link per person

The email is normalized before any uniqueness check. Trim, lowercase, drop everything
from the first plus to the at sign in the local part. For `gmail.com` and
`googlemail.com` also drop dots from the local part and fold both domains to
`gmail.com`. So `a.b+tag@gmail.com` and `ab@googlemail.com` are one person, while
`a.b@fastmail.com` keeps its dot. The raw email and the normalized email are both
stored and the unique index sits on the normalized one.

Claiming runs inside one `BEGIN IMMEDIATE` transaction. It picks the lowest unclaimed
link id, marks it claimed by that entry and commits. Two partial unique indexes back
this up, one so a link can have only one owner and one so an entry can hold only one
link. A returning address gets its original link back. When no link is left the entry
is still recorded and the page says the credit will be emailed. No link is ever
invented.

## Handout controls

The pool is roughly 200 single use links. The site is public, so the one link per person
rule is not enough on its own. It stops one person taking two. It does nothing against a
script that invents a fresh address per request. Four things sit in front of the pool.

**A claim has to come from a session that started on the form.** The cookie is issued at
the email step and signed with HMAC-SHA256. A bare request loop against `/claim` gets
sent back to page 1 and takes nothing. This costs a real attendee nothing, because they
always walk the form. A scripted browser can still walk it, which is what the limits
below are for.

**A per address limit**, keyed on the same truncated client address the visit log uses,
so a /24 for IPv4 and a /64 for IPv6.

**A site wide cap per hour**, the circuit breaker. When it trips it logs at warning level
with the word `CIRCUIT BREAKER TRIPPED`.

**`CLAIMS_OPEN`**, which pauses handouts without taking the site down.

None of these ever show a visitor an error. When any of them withholds a link, the entry
is still recorded, the raffle still counts them, and they see the same "you are on the
list" page as an empty pool. Someone who already holds a link always gets it back, is
never rate limited, and never burns quota, so reloading costs an attendee nothing.

### How the defaults were chosen

A whole room behind one venue NAT is a single truncated address. That is the constraint
that sets the numbers.

| Setting | Default | Why |
| --- | --- | --- |
| `CLAIM_RATE_LIMIT_PER_HOUR` | 120 | Sized for a room, not a person. A 120 person room scanning the QR at once from one venue NAT all get links. A single source script is capped at 120 an hour instead of the whole pool in seconds. |
| `GLOBAL_CLAIM_LIMIT_PER_HOUR` | 150 | Above the per address limit, so a single source binds on its own limit first. Three quarters of a 200 link pool, so at least 50 links survive any one hour of abuse. |

Raise `CLAIM_RATE_LIMIT_PER_HOUR` if the room is bigger than 120 people on one wifi.
Watch `handout` in `/admin/stats` during the event.

Be clear about what these do and do not do. A determined attacker with a scripted browser
and many source addresses is bounded by the site wide cap, not stopped by it. What stops
that is a human seeing the loud log line or the `/admin/stats` numbers and setting
`CLAIMS_OPEN=closed`. The limits exist to slow a drain down and raise the alarm, not to
be a wall.

## Allowed emails

The `allowed_emails` table is empty by default, and while it is empty everybody is
allowed. Load rows into it and any address that is not listed gets a polite page
asking them to check in at the table, with no link claimed. Addresses are normalized
on import the same way.

## Running it

```
docker build -t runpod-signup:test .
docker run -d --name runpod-signup -p 8000:8000 \
  -e DISCORD_INVITE_URL=https://discord.gg/runpod \
  -e ADMIN_TOKEN=your-long-random-token \
  -v /srv/runpod-signup:/data runpod-signup:test
```

Or `docker compose up --build` after writing `.env`. The container runs as uid 1000
and owns `/data`.

The compose file uses a named volume on purpose. A named volume inherits `/data` and
its uid 1000 ownership from the image. A host bind mount does not, and Docker creates
the host directory root owned, so the app cannot open the database and the container
exits with `sqlite3.OperationalError: unable to open database file`. If you bind mount
a host path, as the `docker run` example above does, chown it first:

```
mkdir -p /srv/runpod-signup && chown 1000:1000 /srv/runpod-signup
```

Local development without Docker:

```
python3.12 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt
DATA_DIR=./data ADMIN_TOKEN=dev DISCORD_INVITE_URL=https://discord.gg/runpod \
  .venv/bin/uvicorn app.main:app --reload
```

## Loading the credit links

Put one URL per line in a file and post it. Blank lines are ignored and a URL already
in the table is skipped.

```
cat > links.txt <<'LINKS'
https://runpod.io/redeem/aaaa1111
https://runpod.io/redeem/bbbb2222
https://runpod.io/redeem/cccc3333
LINKS

curl -sS -X POST http://localhost:8000/admin/links \
  -H "Authorization: Bearer $ADMIN_TOKEN" \
  --data-binary @links.txt
# {"added":3,"skipped":0}
```

Links are handed out in id order, so import them in the order you want them used.

## Admin calls

Every call needs `Authorization: Bearer $ADMIN_TOKEN`. A missing or wrong token is
401. An unset `ADMIN_TOKEN` on the server is 503.

Import allowed emails:

```
curl -sS -X POST http://localhost:8000/admin/allowed-emails \
  -H "Authorization: Bearer $ADMIN_TOKEN" \
  --data-binary @attendees.txt
# {"added":42,"skipped":3}
```

Stats, including the handout controls:

```
curl -sS http://localhost:8000/admin/stats -H "Authorization: Bearer $ADMIN_TOKEN"
```

```json
{
  "visits": 128,
  "entries": 41,
  "links_total": 50,
  "links_claimed": 41,
  "links_remaining": 9,
  "handout": {
    "claims_open": true,
    "per_address_limit_per_hour": 120,
    "global_limit_per_hour": 150,
    "claimed_last_hour": 41,
    "breaker_tripped": false,
    "busiest_addresses_last_hour": [{"ip_key": "203.0.113.0", "claims": 39}]
  }
}
```

Raffle draw list as CSV:

```
curl -sS http://localhost:8000/admin/entries.csv \
  -H "Authorization: Bearer $ADMIN_TOKEN" -o raffle.csv
head -2 raffle.csv
```

Columns are `raw_email`, `normalized_email`, `discord_username`, `in_raffle`,
`claimed_link`, `created_at`, `link_claimed_at`.

The raffle entry is the Discord username, not the email. The email only pins a credit
link to a person. `discord_username` is empty when none was given and `in_raffle` is
then `no`, so the draw list is every row where `in_raffle` is `yes`. `/admin/stats`
carries the same split as `raffle_entries` and `entries_without_discord`.

Pause and resume handouts without a restart, if you run it under compose:

```
docker compose run --rm -e CLAIMS_OPEN=closed runpod-signup   # or set it and recreate
```

Health. Without a token it is liveness only, because the pool size is not a stranger's
business. With the admin token it carries the counts:

```
curl -sS http://localhost:8000/health
# {"status":"ok"}

curl -sS http://localhost:8000/health -H "Authorization: Bearer $ADMIN_TOKEN"
# {"status":"ok","links_total":3,"links_remaining":2,"discord_invite_set":true,"admin_configured":true}
```

## Visit tracking

Every page 1 view writes a row with the timestamp, user agent, referrer, a truncated
client IP and the Cloudflare country when it is present. IPv4 keeps three octets and
IPv6 keeps the /64.

The app sits behind Cloudflare and then the GalaxyGate proxy, so the visitor address is
resolved in this order:

1. `Cf-Connecting-Ip`, which Cloudflare sets to the visitor.
2. The first comma separated entry of `X-Forwarded-For`.
3. The socket peer.

`X-Real-Ip` is never used. On this path it carries the Cloudflare edge address, not the
visitor. Headers captured from a live request to https://runpod.galaxygate.app made from
a box whose public address is 144.172.70.17:

```
RemoteAddr: 162.248.103.38:57190
Cf-Connecting-Ip: 144.172.70.17
X-Forwarded-For: 144.172.70.17, 104.23.190.72
X-Real-Ip: 104.23.190.72
X-Forwarded-Proto: https
Cf-Ipcountry: US
```

That request is stored as `144.172.70.0` with country `US`.

## Tests

```
.venv/bin/python -m pytest -q
```

Covers the normalization alias table, the returning visitor, concurrent claims, the
empty pool, the allowlist in both states, admin 401 and 503, visit recording, the words
on the empty pool page, browser back and reload and typed URLs, and the handout controls
including a scripted loop against a normal attendee.
