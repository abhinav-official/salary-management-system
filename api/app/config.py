import os
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

from app.passwords import decode_hash


@dataclass(frozen=True)
class Settings:
    database_path: Path
    hr_email: str
    hr_password_hash: str
    public_origin: str
    cookie_secure: bool = True
    session_seconds: int = 8 * 60 * 60

    def __post_init__(self):
        decode_hash(self.hr_password_hash)
        origin = urlsplit(self.public_origin)
        if (origin.scheme not in {"http", "https"} or not origin.netloc
                or origin.path or origin.query or origin.fragment or origin.username):
            raise ValueError("PUBLIC_ORIGIN must be an exact http(s) origin without a path")
        if self.cookie_secure and origin.scheme != "https":
            raise ValueError("Secure cookies require an HTTPS PUBLIC_ORIGIN")
        if not self.hr_email.strip() or self.session_seconds <= 0:
            raise ValueError("HR email and positive session duration are required")

    @classmethod
    def from_env(cls):
        secure = os.environ.get("COOKIE_SECURE", "true").lower()
        if secure not in {"true", "false"}:
            raise ValueError("COOKIE_SECURE must be true or false")
        return cls(
            database_path=Path(os.environ.get("DATABASE_PATH", "/var/lib/salary/salary.db")),
            hr_email=os.environ["HR_EMAIL"].strip().casefold(),
            hr_password_hash=os.environ["HR_PASSWORD_HASH"],
            public_origin=os.environ["PUBLIC_ORIGIN"],
            cookie_secure=secure == "true",
        )
