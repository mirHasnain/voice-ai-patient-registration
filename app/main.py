"""FastAPI application.

Contains no host-specific code. run.py serves it with uvicorn for local use and
api/index.py exposes it to Vercel.
"""

from __future__ import annotations

import logging
import os
import time
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from . import db, service
from .http import MalformedJSON, fail, ok
from .routes.patients import router as patients_router
from .routes.vapi import router as vapi_router

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("http")

PUBLIC_DIR = Path(__file__).resolve().parent.parent / "public"


def create_app() -> FastAPI:
    app = FastAPI(
        title="Voice AI Patient Registration",
        description="Patient demographics collected by a voice agent, exposed over REST.",
        version="1.0.0",
        docs_url="/docs",
        redoc_url=None,
    )

    # One log line per request, with the status code and duration.
    @app.middleware("http")
    async def access_log(request: Request, call_next):
        started = time.monotonic()
        response = await call_next(request)
        log.info("%s %s -> %s (%dms)", request.method, request.url.path,
                 response.status_code, (time.monotonic() - started) * 1000)
        return response

    @app.get("/health", tags=["ops"])
    async def health():
        """Liveness and configuration check.

        Answers even when the database is unreachable, so a misconfigured
        deployment can be diagnosed. Reports whether each secret is set, never
        its value.
        """
        config = {
            "database_url": db.is_configured(),
            "vapi_webhook_secret": bool(os.environ.get("VAPI_WEBHOOK_SECRET")),
            "vapi_public_key": bool(os.environ.get("VAPI_PUBLIC_KEY")),
            "vapi_assistant_id": bool(os.environ.get("VAPI_ASSISTANT_ID")),
        }
        if not config["database_url"]:
            return fail(503, "DATABASE_URL is not set on this deployment.", {"config": config})
        try:
            return ok({
                "status": "ok",
                "patients": service.count_patients(),
                "config": config,
                "time": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            })
        except Exception as exc:
            log.error("database unreachable: %s", exc)
            return fail(503, f"Database unreachable: {exc}", {"config": config})

    @app.get("/config", tags=["ops"])
    async def public_config():
        """Public values used by the dashboard in the browser."""
        return ok({
            "vapi_public_key": os.environ.get("VAPI_PUBLIC_KEY") or None,
            "vapi_assistant_id": os.environ.get("VAPI_ASSISTANT_ID") or None,
            "phone_number": os.environ.get("PUBLIC_PHONE_NUMBER") or None,
        })

    @app.get("/calls", tags=["voice"])
    async def calls():
        return ok(service.list_calls())

    app.include_router(patients_router)
    app.include_router(vapi_router)

    # --- errors, all in the standard envelope ------------------------------ #

    @app.exception_handler(MalformedJSON)
    async def _malformed(request: Request, exc: MalformedJSON):
        return fail(400, "Request body is not valid JSON.")

    @app.exception_handler(StarletteHTTPException)
    async def _http_error(request: Request, exc: StarletteHTTPException):
        if exc.status_code == 404:
            return fail(404, f"No route for {request.method} {request.url.path}")
        return fail(exc.status_code, str(exc.detail))

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception):
        log.exception("unhandled error: %s", exc)
        return fail(500, "Internal server error.")

    # --- dashboard --------------------------------------------------------- #
    #
    # Served by the platform in production (see vercel.json), so the directory
    # is not present in the deployed function and the mount is conditional.
    if PUBLIC_DIR.is_dir():
        @app.get("/", include_in_schema=False)
        async def dashboard():
            return FileResponse(PUBLIC_DIR / "index.html")

        app.mount("/", StaticFiles(directory=PUBLIC_DIR), name="static")
    else:
        @app.get("/", include_in_schema=False)
        async def dashboard_redirect():
            return RedirectResponse("/index.html")

    return app


app = create_app()
