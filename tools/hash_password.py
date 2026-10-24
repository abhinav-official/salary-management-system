#!/usr/bin/env python3
"""Print a Compose .env entry after securely prompting for the HR password."""

import getpass
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "api"))
from app.passwords import hash_password  # noqa: E402


if __name__ == "__main__":
    password = getpass.getpass("HR password (at least 12 characters): ")
    if len(password) < 12 or password != getpass.getpass("Confirm password: "):
        raise SystemExit("Passwords must match and contain at least 12 characters")
    # Single quotes prevent Compose from interpolating dollar signs in the hash.
    print(f"HR_PASSWORD_HASH='{hash_password(password)}'")
