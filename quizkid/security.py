from __future__ import annotations

import hashlib
import hmac
import os
import secrets
from base64 import urlsafe_b64encode


SESSION_COOKIE = "quizkid_session"
SECRET_KEY = os.environ.get("QUIZKID_SECRET_KEY", "quizkid-dev-secret-key").encode("utf-8")


def hash_password(password: str, salt: bytes | None = None) -> str:
    salt_bytes = salt or os.urandom(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt_bytes, 150000)
    return f"{urlsafe_b64encode(salt_bytes).decode()}${urlsafe_b64encode(digest).decode()}"


def verify_password(password: str, encoded: str) -> bool:
    salt_b64, digest_b64 = encoded.split("$", 1)
    expected = hash_password(password, urlsafe_b64decode(salt_b64))
    return hmac.compare_digest(expected, encoded)


def urlsafe_b64decode(value: str) -> bytes:
    padding = "=" * ((4 - len(value) % 4) % 4)
    return __import__("base64").urlsafe_b64decode(value + padding)


def make_session_token(session_id: str) -> str:
    sig = hmac.new(SECRET_KEY, session_id.encode("utf-8"), hashlib.sha256).hexdigest()
    return f"{session_id}.{sig}"


def read_session_token(token: str) -> str | None:
    if "." not in token:
        return None
    session_id, provided = token.rsplit(".", 1)
    expected = hmac.new(SECRET_KEY, session_id.encode("utf-8"), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, provided):
        return None
    return session_id


def new_session_id() -> str:
    return secrets.token_urlsafe(24)
