import hashlib
import hmac
import secrets
import time

from fastapi import APIRouter, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from app.database import connect
from app.passwords import verify_password

COOKIE_NAME = "salary_session"
router = APIRouter(prefix="/auth", tags=["session"])


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def find_session(request: Request):
    token = request.cookies.get(COOKIE_NAME, "")
    if len(token) != 43:
        return None
    with connect(request.app.state.settings.database_path, existing=True) as connection:
        return connection.execute(
            "SELECT * FROM sessions WHERE token_hash=? AND expires_at>?",
            (token_hash(token), int(time.time())),
        ).fetchone()


class Credentials(BaseModel):
    model_config = ConfigDict(extra="forbid")
    email: str = Field(min_length=1, max_length=254)
    password: str = Field(min_length=1, max_length=1024)


@router.post("/login")
def login(credentials: Credentials, request: Request, response: Response):
    settings = request.app.state.settings
    password_valid = verify_password(credentials.password, settings.hr_password_hash)
    email_valid = hmac.compare_digest(credentials.email.strip().casefold().encode(),
                                      settings.hr_email.casefold().encode())
    if not (password_valid and email_valid):
        return JSONResponse({"detail": "Invalid email or password"}, status_code=401)
    token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
    with connect(settings.database_path, existing=True) as connection:
        connection.execute("BEGIN IMMEDIATE")
        try:
            connection.execute("DELETE FROM sessions WHERE expires_at<=?", (int(time.time()),))
            # Rotate this browser's previous session on a new sign-in.
            old_token = request.cookies.get(COOKIE_NAME, "")
            if old_token:
                connection.execute("DELETE FROM sessions WHERE token_hash=?", (token_hash(old_token),))
            connection.execute("INSERT INTO sessions VALUES (?, ?, ?)",
                               (token_hash(token), csrf, int(time.time()) + settings.session_seconds))
            connection.execute("COMMIT")
        except BaseException:
            connection.execute("ROLLBACK")
            raise
    response.set_cookie(COOKIE_NAME, token, max_age=settings.session_seconds,
                        httponly=True, secure=settings.cookie_secure, samesite="lax", path="/")
    return {"email": settings.hr_email, "csrf_token": csrf}


@router.get("/session")
def session(request: Request):
    return {"email": request.app.state.settings.hr_email,
            "csrf_token": request.state.session["csrf_token"]}


@router.post("/logout", status_code=204)
def logout(request: Request):
    with connect(request.app.state.settings.database_path, existing=True) as connection:
        connection.execute("DELETE FROM sessions WHERE token_hash=?",
                           (request.state.session["token_hash"],))
    response = Response(status_code=204)
    response.delete_cookie(COOKIE_NAME, path="/", httponly=True,
                           secure=request.app.state.settings.cookie_secure, samesite="lax")
    return response
