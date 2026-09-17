# RunPod credit signup

Signup page for the Coffee and Code AI Agent Hackathon, served at
https://runpod.galaxygate.app. An attendee scans the QR code, optionally joins the
RunPod Discord, gives an email, and gets a single use $15 RunPod credit link. Every
entry is a row on the swag raffle list.

Python 3.12, FastAPI, Jinja2, SQLite from the standard library with WAL. Server
rendered, no build step, no client framework, no external fonts or CDNs.

## The flow

1. `GET /` Discord step. The raffle is optional. Invite button plus an optional
   Discord username field. The view is recorded in `visits`.
2. `POST /email` Email step. Shows the Discord username carried over in a hidden field.
3. `POST /claim` Result. Shows the personal credit link, or the "you are on the list"
   page when the pool is empty, or the "we do not have that address" page when the
   allowlist is populated and does not contain the address.

## Environment variables

| Name | Default | Effect |
| --- | --- | --- |
| `DISCORD_INVITE_URL` | unset | The Discord button target. Unset renders the button disabled with the text "Invite link coming soon" and logs a warning at startup. |
| `ADMIN_TOKEN` | unset | Bearer token for every `/admin` route. Unset makes the whole admin surface return 503 and say why. |
| `DATA_DIR` | `/data` | Directory holding `signup.db`. Mount a volume here. |

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

Stats:

```
curl -sS http://localhost:8000/admin/stats -H "Authorization: Bearer $ADMIN_TOKEN"
# {"visits":128,"entries":41,"links_total":50,"links_claimed":41,"links_remaining":9}
```

Raffle draw list as CSV:

```
curl -sS http://localhost:8000/admin/entries.csv \
  -H "Authorization: Bearer $ADMIN_TOKEN" -o raffle.csv
head -2 raffle.csv
```

Columns are `raw_email`, `normalized_email`, `discord_username`, `claimed_link`,
`created_at`, `link_claimed_at`.

Health, which needs no token:

```
curl -sS http://localhost:8000/health
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

Covers the normalization alias table, the returning visitor, concurrent claims,
the empty pool, the allowlist in both states, admin 401 and 503, and visit recording.
