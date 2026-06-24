"""FastAPI application entry point."""
import logging
import os

from fastapi import FastAPI, Depends, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app.api import dashboard, devices, events, labels, security, status, subnets, topology, auth
from app.api.auth import get_current_user
from app.config import get_config
from app.database import init_db
from app.scheduler import start_scheduler, stop_scheduler

# ── Logging ───────────────────────────────────────────────────────────────────
config = get_config()
log_level = getattr(logging, config.get("log_level", "INFO").upper(), logging.INFO)
logging.basicConfig(
    level=log_level,
    format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

# ── App ───────────────────────────────────────────────────────────────────────
app = FastAPI(
    title="NetBeacon",
    version="1.0.0",
    # Disable API docs in production — no external API consumers
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)

# ── CORS: deny all cross-origin requests ──────────────────────────────────────
app.add_middleware(
    CORSMiddleware,
    allow_origins=[],          # no cross-origin access
    allow_credentials=False,
    allow_methods=[],
    allow_headers=[],
)

# ── Security headers middleware ────────────────────────────────────────────────
@app.middleware("http")
async def add_security_headers(request: Request, call_next):
    response: Response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Strict-Transport-Security"] = "max-age=63072000; includeSubDomains"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; "
        "script-src 'self'; "
        "style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data:; "
        "connect-src 'self';"
    )
    response.headers["Permissions-Policy"] = (
        "camera=(), microphone=(), geolocation=(), usb=(), payment=()"
    )
    return response


@app.get("/health", include_in_schema=False)
def health():
    return JSONResponse({"status": "ok"})

# Public
app.include_router(auth.router)
app.include_router(status.router)

# Protected
app.include_router(devices.router,   dependencies=[Depends(get_current_user)])
app.include_router(dashboard.router, dependencies=[Depends(get_current_user)])
app.include_router(topology.router,  dependencies=[Depends(get_current_user)])
app.include_router(events.router,    dependencies=[Depends(get_current_user)])
app.include_router(labels.router,    dependencies=[Depends(get_current_user)])
app.include_router(security.router,  dependencies=[Depends(get_current_user)])
app.include_router(subnets.router,   dependencies=[Depends(get_current_user)])

# Serve frontend static files
_here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FRONTEND_DIR = os.environ.get("FRONTEND_DIR", os.path.join(_here, "web/dist"))

if os.path.isdir(FRONTEND_DIR):
    app.mount("/assets", StaticFiles(directory=os.path.join(FRONTEND_DIR, "assets")), name="assets")

    _safe_root = os.path.realpath(FRONTEND_DIR)

    @app.get("/{rest_of_path:path}", include_in_schema=False)
    async def serve_frontend(rest_of_path: str):
        if rest_of_path.startswith("api/"):
            return JSONResponse({"detail": "Not Found"}, status_code=404)
        file_path = os.path.realpath(os.path.join(FRONTEND_DIR, rest_of_path))
        # Reject anything that escapes the web root (path traversal)
        if not file_path.startswith(_safe_root + os.sep) and file_path != _safe_root:
            return JSONResponse({"detail": "Not Found"}, status_code=404)
        if os.path.isfile(file_path):
            return FileResponse(file_path)
        return FileResponse(os.path.join(FRONTEND_DIR, "index.html"))
else:
    logger.warning("Frontend directory not found at %s. UI will not be available.", FRONTEND_DIR)


# ── Lifecycle ─────────────────────────────────────────────────────────────────

@app.on_event("startup")
async def startup():
    logger.info("Starting NetBeacon")
    init_db()
    _seed_devices_from_config()
    start_scheduler()


@app.on_event("shutdown")
async def shutdown():
    stop_scheduler()
    logger.info("Shutdown complete")


def _seed_devices_from_config():
    from app.database import SessionLocal
    from app.models import Device

    cfg = get_config()
    seed_devices = cfg.get("devices", []) or []
    if not seed_devices:
        return

    with SessionLocal() as db:
        for d in seed_devices:
            ip = d.get("ip") or d.get("ip_address")
            hostname = d.get("hostname", ip)
            if not ip:
                continue
            if db.query(Device).filter(Device.ip_address == ip).first():
                continue
            device = Device(
                hostname=hostname,
                ip_address=ip,
                snmp_community=d.get("snmp_community", cfg.get("snmp_default_community", "public")),
                snmp_version=d.get("snmp_version", cfg.get("snmp_version", "2c")),
                ssh_enabled=d.get("ssh_enabled", False),
                ssh_username=d.get("ssh_username"),
                ssh_password=d.get("ssh_password"),
            )
            db.add(device)
            logger.info("Seeded device from config: %s (%s)", hostname, ip)
        db.commit()
