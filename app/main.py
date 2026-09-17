"""Coffee and Code AI Agent Hackathon signup."""

import csv
import hashlib
import io
import logging
import os
import pathlib
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.exceptions import HTTPException as StarletteHTTPException

from . import config, db, sessions
from .emails import normalize_email, truncate_ip

log = logging.getLogger("runpod_signup")

HERE = os.path.dirname(os.path.abspath(__file__))
STATIC_DIR = os.path.join(HERE, "static")
templates = Jinja2Templates(directory=os.path.join(HERE, "templates"))


def asset(name: str) -> str:
    """A content hashed URL, so a redeploy never leaves a phone on a stale stylesheet."""
    path = os.path.join(STATIC_DIR, name)
    try:
        digest = hashlib.sha256(pathlib.Path(path).read_bytes()).hexdigest()[:10]
    except OSError:
        return f"/static/{name}"
    return f"/static/{name}?v={digest}"


templates.env.globals["asset"] = asset

MAX_DISCORD_LENGTH = 64
MAX_EMAIL_LENGTH = 254

SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "strict-origin-when-cross-origin",
    "X-Frame-Options": "DENY",
    "Content-Security-Policy": (
        "default-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'; "
        "base-uri 'none'; form-action 'self'; frame-ancestors 'none'"
    ),
}


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init_db()
    if not config.discord_invite_url():
        log.warning("DISCORD_INVITE_URL is unset. The Discord button renders disabled.")
    if not config.admin_token():
        log.warning("ADMIN_TOKEN is unset. Every /admin route returns 503.")
    if not config.claims_open():
        log.warning("CLAIMS_OPEN is closed. Entries are recorded but no link is handed out.")
    log.info(
        "Handout limits: %d per address per hour, %d across the site per hour.",
        config.claim_rate_limit_per_hour(),
        config.global_claim_limit_per_hour(),
    )
    yield


app = FastAPI(title="RunPod credit signup", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=os.path.join(HERE, "static")), name="static")


@app.middleware("http")
async def add_security_headers(request: Request, call_next):
    response = await call_next(request)
    for name, value in SECURITY_HEADERS.items():
        response.headers.setdefault(name, value)
    if request_is_https(request):
        response.headers.setdefault("Strict-Transport-Security", "max-age=31536000")
    return response


def request_is_https(request: Request) -> bool:
    forwarded = (request.headers.get("x-forwarded-proto") or "").split(",")[0].strip().lower()
    return forwarded == "https" or request.url.scheme == "https"


def client_ip(request: Request) -> str | None:
    """Resolve the visitor address behind Cloudflare and the GalaxyGate proxy.

    Cf-Connecting-Ip carries the visitor. X-Real-Ip carries the Cloudflare edge, so it
    is never used. X-Forwarded-For carries the visitor first and the edge after it.
    """
    cloudflare = (request.headers.get("cf-connecting-ip") or "").strip()
    if cloudflare:
        return cloudflare
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded and forwarded.split(",")[0].strip():
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else None


def ip_key(request: Request) -> str:
    """The rate limit bucket. Same truncation as the visit log, /24 or /64."""
    return truncate_ip(client_ip(request)) or "unknown"


def page(request: Request, name: str, status_code: int = 200, **context) -> HTMLResponse:
    response = templates.TemplateResponse(request, name, context, status_code=status_code)
    response.headers["Cache-Control"] = "no-store"
    return response


def read_session(request: Request) -> dict:
    return sessions.load(request.cookies.get(sessions.COOKIE_NAME))


def write_session(request: Request, response: Response, payload: dict) -> Response:
    if not payload:
        response.delete_cookie(sessions.COOKIE_NAME, path="/")
        return response
    response.set_cookie(
        sessions.COOKIE_NAME,
        sessions.dump(payload),
        max_age=sessions.COOKIE_MAX_AGE,
        httponly=True,
        samesite="lax",
        secure=request_is_https(request),
        path="/",
    )
    return response


def redirect(request: Request, target: str, payload: dict | None = None) -> RedirectResponse:
    """See other, so a reload re-fetches the page instead of re-posting the form."""
    response = RedirectResponse(target, status_code=303)
    response.headers["Cache-Control"] = "no-store"
    if payload is not None:
        write_session(request, response, payload)
    return response


@app.get("/", response_class=HTMLResponse)
def step_discord(request: Request, restart: str = ""):
    db.record_visit(
        request.headers.get("user-agent"),
        request.headers.get("referer"),
        truncate_ip(client_ip(request)),
        (request.headers.get("cf-ipcountry") or "").strip() or None,
    )
    session = read_session(request)
    return page(
        request,
        "step1.html",
        step=1,
        invite=config.discord_invite_url(),
        discord=session.get("d", ""),
        restart=bool(restart),
    )


@app.post("/email")
def start_email_step(request: Request, discord_username: str = Form("")):
    session = read_session(request)
    session["d"] = discord_username.strip()[:MAX_DISCORD_LENGTH]
    session.pop("err", None)
    return redirect(request, "/email", session)


@app.get("/email", response_class=HTMLResponse)
def step_email(request: Request):
    session = read_session(request)
    if not session:
        return redirect(request, "/")
    error = session.pop("err", None)
    typed = session.get("raw", "")
    rendered = page(
        request,
        "step2.html",
        step=2,
        discord=session.get("d", ""),
        email=typed,
        error=error,
    )
    if error:
        write_session(request, rendered, session)
    return rendered


@app.post("/claim")
def submit(request: Request, email: str = Form(...), discord_username: str = Form("")):
    session = read_session(request)
    if not session:
        # A claim has to come from a session that started on the form. This costs a real
        # attendee nothing and it stops a bare request loop, which is the cheap drain.
        # A scripted browser can still walk the form, which is what the rate limit and
        # the site wide cap below are for.
        log.info("Claim with no session from %s. Sending them back to the start.",
                 ip_key(request))
        return redirect(request, "/?restart=1", {"d": ""})
    address = email.strip()
    discord = (discord_username or session.get("d") or "").strip()[:MAX_DISCORD_LENGTH]
    session["d"] = discord
    session["raw"] = address[:MAX_EMAIL_LENGTH]

    problem = email_problem(address)
    if problem:
        session["err"] = problem
        return redirect(request, "/email", session)

    session.pop("err", None)
    session.pop("raw", None)
    normalized = normalize_email(address)
    if not db.is_allowed(normalized):
        session["blocked"] = address
        session.pop("e", None)
        return redirect(request, "/claim", session)

    result = db.claim(
        address,
        discord,
        ip_key=ip_key(request),
        per_ip_limit=config.claim_rate_limit_per_hour(),
        global_limit=config.global_claim_limit_per_hour(),
        claims_open=config.claims_open(),
    )
    log_withheld(request, result)
    session.pop("blocked", None)
    session["e"] = result["normalized_email"]
    session["new"] = not result["returning"]
    return redirect(request, "/claim", session)


@app.get("/claim", response_class=HTMLResponse)
def result(request: Request):
    session = read_session(request)
    if session.get("blocked"):
        return page(request, "not_allowed.html", step=3, email=session["blocked"])
    normalized = session.get("e")
    if not normalized:
        return redirect(request, "/")
    entry = db.find_entry(normalized)
    if entry is None:
        return redirect(request, "/", {})
    if entry["link_url"]:
        return page(
            request,
            "result.html",
            step=3,
            link=entry["link_url"],
            email=entry["raw_email"],
            returning=not session.get("new", False),
        )
    return page(request, "no_link.html", step=3, email=entry["raw_email"])


def email_problem(address: str) -> str | None:
    if len(address) > MAX_EMAIL_LENGTH:
        return f"That address is longer than {MAX_EMAIL_LENGTH} characters. Please shorten it."
    if "@" not in address or "." not in address.rpartition("@")[2]:
        return "That does not look like an email address. Please check it."
    local, _, domain = address.rpartition("@")
    if not local or not domain or " " in address:
        return "That does not look like an email address. Please check it."
    return None


def log_withheld(request: Request, result: dict) -> None:
    reason = result["withheld_because"]
    if reason == "global_limit":
        log.warning(
            "CIRCUIT BREAKER TRIPPED. %d links already claimed this hour, the cap is %d. "
            "No further links will be handed out until the hour rolls. Address %s.",
            db.claims_in_last_hour(),
            config.global_claim_limit_per_hour(),
            ip_key(request),
        )
    elif reason == "ip_limit":
        log.warning(
            "Rate limit hit. Address %s has taken %d links this hour, the cap is %d. "
            "Entry recorded without a link.",
            ip_key(request),
            db.claims_in_last_hour(ip_key(request)),
            config.claim_rate_limit_per_hour(),
        )
    elif reason == "claims_closed":
        log.info("Claims are closed. Entry recorded without a link.")


@app.get("/health")
def health(request: Request):
    """Public callers get liveness only. The counts need the admin token.

    The pool size and whether an admin surface exists are not a stranger's business.
    """
    counts = db.stats()
    body = {"status": "ok"}
    if config.admin_token() and presented_token(request) == config.admin_token():
        body.update(
            {
                "links_total": counts["links_total"],
                "links_remaining": counts["links_remaining"],
                "discord_invite_set": bool(config.discord_invite_url()),
                "admin_configured": True,
            }
        )
    return body


def presented_token(request: Request) -> str:
    scheme, _, presented = request.headers.get("authorization", "").partition(" ")
    return presented.strip() if scheme.lower() == "bearer" else ""


def require_admin(request: Request) -> None:
    token = config.admin_token()
    if not token:
        raise HTTPException(
            status_code=503,
            detail="ADMIN_TOKEN is not set on this server, so the admin surface is disabled.",
        )
    if presented_token(request) != token:
        raise HTTPException(status_code=401, detail="Send a valid bearer token.")


FORM_FIELDS = ("items", "links", "emails")


async def body_text(request: Request) -> str:
    """Newline separated payload.

    Accepts a raw request body, which is what curl --data-binary sends, and also an
    items, links or emails form field. curl labels a raw body as form encoded by
    default, so a form parse that finds none of those fields falls back to the body.
    """
    raw = await request.body()
    content_type = request.headers.get("content-type", "")
    if content_type.startswith(("application/x-www-form-urlencoded", "multipart/form-data")):
        form = await request.form()
        for field in FORM_FIELDS:
            value = form.get(field)
            if isinstance(value, str):
                return value
    return raw.decode("utf-8", "replace")


@app.post("/admin/links", dependencies=[Depends(require_admin)])
async def admin_import_links(request: Request):
    return JSONResponse(db.import_links(await body_text(request)))


@app.post("/admin/allowed-emails", dependencies=[Depends(require_admin)])
async def admin_import_allowed(request: Request):
    return JSONResponse(db.import_allowed_emails(await body_text(request)))


@app.get("/admin/stats", dependencies=[Depends(require_admin)])
def admin_stats():
    counts = db.stats()
    global_limit = config.global_claim_limit_per_hour()
    claimed_this_hour = db.claims_in_last_hour()
    counts["handout"] = {
        "claims_open": config.claims_open(),
        "per_address_limit_per_hour": config.claim_rate_limit_per_hour(),
        "global_limit_per_hour": global_limit,
        "claimed_last_hour": claimed_this_hour,
        "breaker_tripped": claimed_this_hour >= global_limit,
        "busiest_addresses_last_hour": db.busiest_addresses_last_hour(),
    }
    return JSONResponse(counts)


@app.get("/admin/entries.csv", dependencies=[Depends(require_admin)])
def admin_entries_csv():
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(
        [
            "raw_email",
            "normalized_email",
            "discord_username",
            "claimed_link",
            "created_at",
            "link_claimed_at",
        ]
    )
    for row in db.export_entries():
        writer.writerow(
            [
                row["raw_email"],
                row["normalized_email"],
                row["discord_username"] or "",
                row["claimed_link"] or "",
                row["created_at"],
                row["link_claimed_at"] or "",
            ]
        )
    return Response(
        content=buffer.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="entries.csv"'},
    )


def wants_json(request: Request) -> bool:
    return "text/html" not in request.headers.get("accept", "")


@app.exception_handler(StarletteHTTPException)
async def friendly_errors(request: Request, exc: StarletteHTTPException):
    """A browser never sees raw JSON. Tools still get JSON."""
    if request.url.path.startswith("/admin") and wants_json(request):
        return JSONResponse({"detail": exc.detail}, status_code=exc.status_code)
    if exc.status_code in (404, 405) and not request.url.path.startswith("/admin"):
        return page(request, "error.html", status_code=404, heading="Page not found",
                    message="That address is not part of the signup.")
    return page(request, "error.html", status_code=exc.status_code,
                heading="Something went wrong", message=str(exc.detail))
