"""Password hashing shared by setup and authentication; no plaintext persistence."""

import base64
import hashlib
import hmac
import secrets


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    key = hashlib.scrypt(password.encode(), salt=salt, n=16384, r=8, p=1, dklen=32)
    return "scrypt$" + "$".join(base64.b64encode(value).decode() for value in (salt, key))


def decode_hash(encoded: str) -> tuple[bytes, bytes]:
    kind, salt, key = encoded.split("$")
    decoded = base64.b64decode(salt, validate=True), base64.b64decode(key, validate=True)
    if kind != "scrypt" or len(decoded[0]) != 16 or len(decoded[1]) != 32:
        raise ValueError("Invalid password hash")
    return decoded


def verify_password(password: str, encoded: str) -> bool:
    salt, expected = decode_hash(encoded)
    actual = hashlib.scrypt(password.encode(), salt=salt, n=16384, r=8, p=1, dklen=32)
    return hmac.compare_digest(actual, expected)
