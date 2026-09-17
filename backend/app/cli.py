"""Operational CLI utilities (not HTTP User CRUD)."""

from __future__ import annotations

import argparse
import getpass
import sys

from sqlalchemy import select

from app.core.security import hash_password
from app.db.models.user import AppUser
from app.db.session import SessionLocal
from app.modules.auth.repository import AuthRepository


def create_admin(login_id: str, name: str, email: str | None, department: str | None) -> int:
    password = getpass.getpass("Password: ")
    password_confirm = getpass.getpass("Confirm password: ")
    if not password:
        print("Password must not be empty.", file=sys.stderr)
        return 1
    if password != password_confirm:
        print("Passwords do not match.", file=sys.stderr)
        return 1

    with SessionLocal() as db:
        repo = AuthRepository(db)
        existing = repo.get_by_login_id(login_id)
        if existing is not None:
            print(f"User already exists: {login_id}", file=sys.stderr)
            return 1

        user = repo.create_user(
            login_id=login_id,
            password_hash=hash_password(password),
            name=name,
            role="ADMIN",
            email=email,
            department=department,
        )
        db.commit()
        print(f"Created ADMIN user id={user.id} login_id={user.login_id}")
    return 0


def has_admin(*, quiet: bool = False) -> int:
    """Return success when at least one ACTIVE ADMIN account exists."""
    with SessionLocal() as db:
        admin_id = db.execute(
            select(AppUser.id)
            .where(AppUser.role == "ADMIN", AppUser.status == "ACTIVE")
            .limit(1)
        ).scalar_one_or_none()

    exists = admin_id is not None
    if not quiet:
        print("ACTIVE ADMIN user exists." if exists else "No ACTIVE ADMIN user exists.")
    return 0 if exists else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="app.cli", description="TalentScope CLI")
    sub = parser.add_subparsers(dest="command", required=True)

    create = sub.add_parser("create-admin", help="Create an initial ADMIN user")
    create.add_argument("--login-id", required=True)
    create.add_argument("--name", required=True)
    create.add_argument("--email", default=None)
    create.add_argument("--department", default=None)

    check = sub.add_parser(
        "has-admin",
        help="Exit 0 when an ACTIVE ADMIN exists, otherwise exit 1",
    )
    check.add_argument("--quiet", action="store_true")

    args = parser.parse_args(argv)
    if args.command == "create-admin":
        return create_admin(args.login_id, args.name, args.email, args.department)
    if args.command == "has-admin":
        return has_admin(quiet=args.quiet)
    parser.error(f"Unknown command: {args.command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
