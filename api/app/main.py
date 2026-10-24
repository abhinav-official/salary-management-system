import hmac
import sqlite3
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.auth import find_session, router
from app.config import Settings
from app.database import check_ready


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings.from_env()

    @asynccontextmanager
    async def lifespan(app):
        check_ready(settings.database_path)
        yield

    app = FastAPI(title="Salary Management API", lifespan=lifespan)
    app.state.settings = settings

    @app.middleware("http")
    async def security_boundary(request: Request, call_next):
        public = (request.method, request.url.path) in {
            ("GET", "/health/live"), ("GET", "/health/ready"), ("POST", "/auth/login"),
        }
        mutation = request.method not in {"GET", "HEAD", "OPTIONS"}
        response = None
        if mutation and request.headers.get("origin") != settings.public_origin:
            response = JSONResponse({"detail": "Untrusted request origin"}, status_code=403)
        if response is None and not public:
            try:
                request.state.session = find_session(request)
            except sqlite3.Error:
                response = JSONResponse({"detail": "API temporarily unavailable"}, status_code=503)
            if response is None and request.state.session is None:
                response = JSONResponse({"detail": "Sign in required"}, status_code=401)
            elif response is None and mutation and not hmac.compare_digest(
                request.headers.get("x-csrf-token", "").encode(),
                request.state.session["csrf_token"].encode(),
            ):
                response = JSONResponse({"detail": "Invalid CSRF token"}, status_code=403)
        if response is None:
            response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        return response

    @app.get("/health/live", tags=["health"])
    def live():
        return {"status": "alive"}

    @app.get("/health/ready", tags=["health"])
    def ready():
        try:
            check_ready(settings.database_path)
        except (sqlite3.Error, RuntimeError, OSError):
            return JSONResponse({"status": "not_ready"}, status_code=503)
        return {"status": "ready"}

    app.include_router(router)
    return app
