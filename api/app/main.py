import hmac
import sqlite3
from contextlib import asynccontextmanager
from dataclasses import replace

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.auth import find_session, router
from app.accounts import initialize_account
from app.analytics_routes import router as analytics_router
from app.band_routes import router as band_router
from app.bands import BandError
from app.config import Settings
from app.database import check_ready
from app.employee_routes import router as employee_router
from app.employees import EmployeeError
from app.equity_routes import router as equity_router
from app.import_routes import router as import_router
from app.xlsx import ImportProblem


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings.from_env()

    @asynccontextmanager
    async def lifespan(app):
        check_ready(settings.database_path)
        email, password_hash = initialize_account(settings.database_path, settings.hr_email, settings.hr_password_hash)
        app.state.settings = replace(settings, hr_email=email, hr_password_hash=password_hash)
        yield

    app = FastAPI(title="Salary Management API", lifespan=lifespan)
    app.state.settings = settings

    @app.exception_handler(ImportProblem)
    async def import_error(request: Request, error: ImportProblem):
        return JSONResponse({"detail": error.detail}, status_code=error.status)

    @app.exception_handler(EmployeeError)
    async def employee_error(request: Request, error: EmployeeError):
        return JSONResponse({"detail": error.detail}, status_code=error.status)

    @app.exception_handler(BandError)
    async def band_error(request: Request, error: BandError):
        return JSONResponse({"detail": error.detail}, status_code=error.status)

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
    app.include_router(band_router)
    app.include_router(employee_router)
    app.include_router(analytics_router)
    app.include_router(equity_router)
    app.include_router(import_router)
    return app
