"""Users, tokens, Sign-ins, Grants. Effective databases = grants ∩ live Registry, computed at request time.

Users, Grants and Sign-ins live in the System Store; tokens live in the environment, never in a row. Every change an admin
makes is a line in the audit trail, with the admin's name on it.
"""

import os
import re
import secrets
import sqlite3
from contextlib import closing
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Iterable, Literal

from pydantic import BaseModel

from data_agents.system import store


class User(BaseModel):
    name: str
    role: Literal["analyst", "admin"] = "analyst"
    grants: list[str] = []


class AuditEntry(BaseModel):
    at: str
    actor: str
    action: str
    user: str | None = None
    database: str | None = None


class NotAuthenticated(PermissionError):
    pass


class NotAuthorized(PermissionError):
    pass


def _open(registry_dir: Path | None) -> closing:
    return closing(store.connect(registry_dir=registry_dir))


def _grants(conn: sqlite3.Connection, name: str) -> list[str]:
    return [db for (db,) in conn.execute("SELECT database FROM grants WHERE user = ? ORDER BY database", (name,))]


def load_users(registry_dir: Path | None = None) -> dict[str, User]:
    with _open(registry_dir) as conn:
        return {name: User(name=name, role=role, grants=_grants(conn, name)) for name, role in conn.execute("SELECT name, role FROM users ORDER BY name")}


def user(name: str, registry_dir: Path | None = None) -> User:
    users = load_users(registry_dir)
    if name not in users:
        raise KeyError(f"no user {name}")
    return users[name]


def list_users(q: str = "", page: int = 1, size: int = 10, registry_dir: Path | None = None) -> tuple[list[User], int]:
    """One page of the Users whose name contains q, and how many match: the shape a list of fifty, or fifty thousand, needs."""
    like, offset = f"%{q}%", (max(page, 1) - 1) * size
    with _open(registry_dir) as conn:
        total = conn.execute("SELECT COUNT(*) FROM users WHERE name LIKE ?", (like,)).fetchone()[0]
        rows = conn.execute("SELECT name, role FROM users WHERE name LIKE ? ORDER BY name LIMIT ? OFFSET ?", (like, size, offset)).fetchall()
        return [User(name=name, role=role, grants=_grants(conn, name)) for name, role in rows], total


def grantees(database: str, registry_dir: Path | None = None) -> list[str]:
    with _open(registry_dir) as conn:
        return [name for (name,) in conn.execute("SELECT user FROM grants WHERE database = ? ORDER BY user", (database,))]


def audit(limit: int = 20, registry_dir: Path | None = None) -> list[AuditEntry]:
    with _open(registry_dir) as conn:
        rows = conn.execute("SELECT at, actor, action, user, database FROM audit ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [AuditEntry(at=r[0], actor=r[1], action=r[2], user=r[3], database=r[4]) for r in rows]


def token_variable(name: str) -> str:
    return f"DATA_AGENTS_TOKEN_{name.upper()}"


def authenticate(token: str | None, registry_dir: Path | None = None) -> User:
    """Map a bearer token to a User. Tokens live in the environment, never in the store."""
    for candidate in load_users(registry_dir).values():
        expected = os.environ.get(token_variable(candidate.name))
        if token and expected and secrets.compare_digest(token.encode(), expected.encode()):  # bytes: a non-ASCII token compares false, never raises
            return candidate
    raise NotAuthenticated("unknown token")


SIGN_IN_HOURS = 8  # a working day; the browser presents the Sign-in's id, and the token itself never leaves the form


def sign_in(token: str | None, registry_dir: Path | None = None) -> str:
    """Exchange a token for a Sign-in: a random id the browser holds instead of the credential. Raises NotAuthenticated."""
    user = authenticate(token, registry_dir)
    sign_in_id, at = secrets.token_urlsafe(32), datetime.now(UTC)
    with _open(registry_dir) as conn, conn:
        conn.execute("DELETE FROM sign_ins WHERE expires_at < ?", (at.isoformat(),))  # housekeeping rides on the write we make anyway
        conn.execute("INSERT INTO sign_ins VALUES (?, ?, ?, ?)", (sign_in_id, user.name, at.isoformat(), (at + timedelta(hours=SIGN_IN_HOURS)).isoformat()))
    return sign_in_id


def signed_in_user(sign_in_id: str | None, registry_dir: Path | None = None) -> User:
    """The User behind a Sign-in id, if it exists, has not expired, and the User still does. Raises NotAuthenticated."""
    if not sign_in_id:
        raise NotAuthenticated("not signed in")
    with _open(registry_dir) as conn:
        row = conn.execute("SELECT user FROM sign_ins WHERE id = ? AND expires_at >= ?", (sign_in_id, datetime.now(UTC).isoformat())).fetchone()
    if row:
        try:
            return user(row[0], registry_dir)
        except KeyError:  # the User was removed after signing in
            pass
    raise NotAuthenticated("sign-in expired or unknown")


def sign_out(sign_in_id: str | None, registry_dir: Path | None = None) -> None:
    with _open(registry_dir) as conn, conn:
        conn.execute("DELETE FROM sign_ins WHERE id = ?", (sign_in_id,))


def effective_databases(user: User, registered: Iterable[str]) -> list[str]:
    """grants ∩ live Registry — and for an admin, the live Registry itself: an admin holds every database by role, not by grant."""
    if user.role == "admin":
        return list(registered)
    live = set(registered)
    return [name for name in user.grants if name in live]


def authorize(user: User, database: str, registered: Iterable[str]) -> None:
    if database not in effective_databases(user, registered):
        raise NotAuthorized(f"{user.name} may not use {database}")


def require_admin(user: User) -> None:
    if user.role != "admin":
        raise NotAuthorized(f"{user.name} is not an admin")


NAME = re.compile(r"[A-Za-z0-9_-]+")


def check_name(name: str, what: str) -> None:
    """Users name token variables and databases name files and URL segments: letters, digits, - and _ only, so neither can escape."""
    if not NAME.fullmatch(name):
        raise ValueError(f"{name!r} cannot name a {what}: letters, digits, - and _ only")


def create_user(name: str, role: str, by: str, registry_dir: Path | None = None) -> User:
    """A new principal. Signing in still needs DATA_AGENTS_TOKEN_<NAME> in the environment: the store holds no secret."""
    created = User(name=name, role=role)  # validates the role
    check_name(name, "user")
    with _open(registry_dir) as conn, conn:
        if conn.execute("SELECT 1 FROM users WHERE name = ?", (name,)).fetchone():
            raise ValueError(f"user {name} already exists")
        conn.execute("INSERT INTO users VALUES (?, ?)", (name, created.role))
        store.record(conn, by, "create user", user=name)
    return created


def remove_user(name: str, by: str, registry_dir: Path | None = None) -> None:
    with _open(registry_dir) as conn, conn:
        if conn.execute("DELETE FROM users WHERE name = ?", (name,)).rowcount == 0:
            raise KeyError(f"no user {name}")
        conn.execute("DELETE FROM grants WHERE user = ?", (name,))
        conn.execute("DELETE FROM sign_ins WHERE user = ?", (name,))  # a removed User is signed out everywhere, now
        store.record(conn, by, "remove user", user=name)


def _grantable(conn: sqlite3.Connection, user_name: str) -> None:
    row = conn.execute("SELECT role FROM users WHERE name = ?", (user_name,)).fetchone()
    if row is None:
        raise KeyError(f"no user {user_name}")
    if row[0] == "admin":
        raise ValueError(f"{user_name} is an admin and holds every database; there is nothing to grant or revoke")


def grant(user_name: str, database: str, by: str, registry_dir: Path | None = None) -> None:
    with _open(registry_dir) as conn, conn:
        _grantable(conn, user_name)
        if conn.execute("INSERT OR IGNORE INTO grants VALUES (?, ?, ?, ?)", (user_name, database, by, store.now())).rowcount:
            store.record(conn, by, "grant", user=user_name, database=database)


def revoke(user_name: str, database: str, by: str, registry_dir: Path | None = None) -> None:
    with _open(registry_dir) as conn, conn:
        _grantable(conn, user_name)
        if conn.execute("DELETE FROM grants WHERE user = ? AND database = ?", (user_name, database)).rowcount:
            store.record(conn, by, "revoke", user=user_name, database=database)


def revoke_everyone(database: str, by: str, registry_dir: Path | None = None) -> None:
    for name in grantees(database, registry_dir):
        revoke(name, database, by, registry_dir)
