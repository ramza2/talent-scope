"""Password hashing and opaque token helpers.

Browser auth uses Redis server sessions (not JWT).
Passwords are hashed with Argon2id via argon2-cffi.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

_ph = PasswordHasher()

# Used only to equalize timing when a user row is missing.
_DUMMY_PASSWORD_HASH = _ph.hash("talentscope-dummy-password-not-a-real-secret")


def hash_password(password: str) -> str:
    return _ph.hash(password)


def verify_password(password: str, password_hash: str | None) -> bool:
    """Verify password against Argon2 hash.

    When ``password_hash`` is missing/invalid, still runs a dummy verification
    to reduce obvious timing differences for unknown users.
    """
    if not password_hash:
        try:
            _ph.verify(_DUMMY_PASSWORD_HASH, password)
        except (VerifyMismatchError, VerificationError, InvalidHashError):
            pass
        return False

    try:
        return bool(_ph.verify(password_hash, password))
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False
    except Exception:
        return False


def generate_session_id() -> str:
    """Opaque session id with >= 256-bit entropy."""
    return secrets.token_urlsafe(32)


def generate_csrf_token() -> str:
    return secrets.token_urlsafe(32)


def hash_session_id(session_id: str) -> str:
    """SHA-256 hex digest used as Redis lookup key material."""
    return hashlib.sha256(session_id.encode("utf-8")).hexdigest()


def constant_time_equals(left: str, right: str) -> bool:
    return hmac.compare_digest(left.encode("utf-8"), right.encode("utf-8"))
