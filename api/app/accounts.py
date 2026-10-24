"""Bootstrap the single HR identity once; never reset an existing account."""

from pathlib import Path

from app.database import transaction
from app.passwords import hash_password

DEMO_EMAIL = "demo@acme.demo"
DEMO_PASSWORD = "Demo-2026-Salary"


def initialize_account(path: Path, email: str, password_hash: str) -> tuple[str, str]:
    # Serialize first-start contenders before checking existence. No upsert,
    # update, or password rehash occurs when an account is already present.
    with transaction(path) as connection:
        existing = connection.execute("SELECT email, password_hash FROM hr_account WHERE singleton=1").fetchone()
        if existing is not None:
            return existing["email"], existing["password_hash"]
        email = email.strip().casefold()
        if not email:
            email, password_hash = DEMO_EMAIL, hash_password(DEMO_PASSWORD)
        connection.execute("INSERT INTO hr_account VALUES (1, ?, ?)", (email, password_hash))
        return email, password_hash
