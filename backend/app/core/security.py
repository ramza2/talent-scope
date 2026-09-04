"""Security helpers placeholder.

Auth is session-cookie based per docs/15_backend_api.md.
JWT is not used. Full login/session implementation is out of skeleton scope.
"""

from typing import Any


def hash_password(password: str) -> str:
    """Placeholder password hashing — replace with a real KDF in auth module."""
    raise NotImplementedError("Password hashing is not implemented in the skeleton")


def verify_password(password: str, password_hash: str) -> bool:
    raise NotImplementedError("Password verification is not implemented in the skeleton")


def get_current_user_placeholder() -> dict[str, Any]:
    """Dependency stub for future RBAC checks."""
    raise NotImplementedError("Authentication is not implemented in the skeleton")
