"""Coffee and Code AI Agent Hackathon signup."""

import csv
import io
import logging
import os
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from . import db
from .emails import normalize_email, truncate_ip

log = logging.getLogger("runpod_signup")

HERE = os.path.dirname(os.path.abspath(__file__))
templates = Jinja2Templates(directory=os.path.join(HERE, "templates"))


def discord_invite_url() -> str:
    return (os.environ.get("DISCORD_INVITE_URL") or "").strip()


def admin_token() -> str:
    return (os.environ.get("ADMIN_TOKEN") or "").strip()


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init_db()
    if not discord_invite_url():
        log.warning("DISCORD_INVITE_URL is unset. The Discord button renders disabled.")
    if not admin_token():
        log.warning("ADMIN_TOKEN is unset. Every /admin route returns 503.")
    yield


app = FastAPI(title="RunPod credit signup", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=os.path.join(HERE, "static")), name="static")


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


def page(request: Request, name: str, **context) -> HTMLResponse:
    return templates.TemplateResponse(request, name, context)


@app.get("/", response_class=HTMLResponse)
def step_discord(request: Request):
    db.record_visit(
        request.headers.get("user-agent"),
        request.headers.get("referer"),
        truncate_ip(client_ip(request)),
        (request.headers.get("cf-ipcountry") or "").strip() or None,
    )
    return page(request, "step1.html", step=1, invite=discord_invite_url(), discord="")


@app.post("/email", response_class=HTMLResponse)
def step_email(request: Request, discord_username: str = Form("")):
    return page(request, "step2.html", step=2, discord=discord_username.strip())


@app.post("/claim", response_class=HTMLResponse)
def submit(request: Request, email: str = Form(...), discord_username: str = Form("")):
    address = email.strip()
    if "@" not in address or "." not in address.rpartition("@")[2]:
        return page(
            request,
            "step2.html",
            step=2,
            discord=discord_username.strip(),
            email=address,
            error="That does not look like an email address. Please check it.",
        )
    normalized = normalize_email(address)
    if not db.is_allowed(normalized):
        return page(request, "not_allowed.html", step=3, email=address)
    result = db.claim(address, discord_username)
    if result["link_url"] is None:
        return page(request, "no_link.html", step=3, discord=discord_username.strip())
    return page(
        request,
        "result.html",
        step=3,
        link=result["link_url"],
        returning=result["returning"],
        discord=discord_username.strip(),
    )


@app.get("/health")
def health():
    counts = db.stats()
    return {
        "status": "ok",
        "links_total": counts["links_total"],
        "links_remaining": counts["links_remaining"],
        "discord_invite_set": bool(discord_invite_url()),
        "admin_configured": bool(admin_token()),
    }


def require_admin(request: Request) -> None:
    token = admin_token()
    if not token:
        raise HTTPException(
            status_code=503,
            detail="ADMIN_TOKEN is not set on this server, so the admin surface is disabled.",
        )
    header = request.headers.get("authorization", "")
    scheme, _, presented = header.partition(" ")
    if scheme.lower() != "bearer" or presented.strip() != token:
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
    return JSONResponse(db.stats())


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
