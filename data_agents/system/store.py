"""The System Store: one SQLite file for the system's own state — Users, Grants, the Registry, Sessions, Turns, the audit trail.

Not a Database: nothing in it is ever queried by an agent. It sits in the registry directory beside the data files and the
Schema Documents, so REGISTRY_DIR names all of it. The YAML files that used to be the Registry and the users file are now
seeds: imported when they appear or change, in the way provisioning files work — edit the file and the store follows, edit the
store and the file stays what it was. The old sessions.db beside the registry directory is adopted once, the first time.

Why SQLite: one file, transactional (YAML was not), one writer at a time enforced rather than hoped, and a test can point at a
temporary one. This module is the seam: every connection is made here, the schema is plain SQL, and Postgres would replace
`connect` and the `?` placeholders, which is what a second replica on another host would need.
"""

import hashlib
import os
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import yaml

DIR = Path(os.environ.get("REGISTRY_DIR", "registry"))
FILE = "system.db"

# Applied in order; PRAGMA user_version counts how many have run. Sessions and Turns are created IF NOT EXISTS with the columns
# added by name, so an old sessions.db opened as a store (the benchmark's, or a test's) migrates in place.
MIGRATIONS = [
    """
    CREATE TABLE users (name TEXT PRIMARY KEY, role TEXT NOT NULL DEFAULT 'analyst');
    CREATE TABLE grants (user TEXT NOT NULL, database TEXT NOT NULL, granted_by TEXT NOT NULL, granted_at TEXT NOT NULL,
                         PRIMARY KEY (user, database));
    CREATE TABLE databases (name TEXT PRIMARY KEY, url TEXT NOT NULL, description TEXT NOT NULL DEFAULT '', schema_document TEXT NOT NULL);
    CREATE TABLE audit (id INTEGER PRIMARY KEY, at TEXT NOT NULL, actor TEXT NOT NULL, action TEXT NOT NULL, user TEXT, database TEXT);
    CREATE TABLE seeds (file TEXT PRIMARY KEY, sha TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS sessions (
        id TEXT PRIMARY KEY, user TEXT NOT NULL, database TEXT NOT NULL, created_at TEXT NOT NULL,
        turns INTEGER NOT NULL DEFAULT 0, messages BLOB
    );
    CREATE TABLE IF NOT EXISTS turns (
        session_id TEXT NOT NULL REFERENCES sessions(id), n INTEGER NOT NULL, created_at TEXT NOT NULL,
        question TEXT NOT NULL, result_kind TEXT NOT NULL, result TEXT NOT NULL, metrics TEXT NOT NULL,
        PRIMARY KEY (session_id, n)
    );
    """,
    # A Sign-in: the browser holds its id, never the token. Expiry is a column so a row outlives nothing it should not.
    "CREATE TABLE sign_ins (id TEXT PRIMARY KEY, user TEXT NOT NULL, created_at TEXT NOT NULL, expires_at TEXT NOT NULL);",
]
TURN_COLUMNS = {"chart_spec": "TEXT", "vega_lite": "TEXT", "renderer": "TEXT"}  # the Chart Spec is the record; the other two its cache


def path(registry_dir: Path | None = None) -> Path:
    return (registry_dir or DIR) / FILE


def connect(at: Path | None = None, registry_dir: Path | None = None) -> sqlite3.Connection:
    """Open the store at `at` (default: the one in the registry directory), migrated and seeded. Close it when done."""
    seeds_from = registry_dir or (DIR if at is None else None)  # only the registry directory's own store has seeds
    at = at or path(seeds_from)
    conn = sqlite3.connect(at, check_same_thread=False)  # a Turn runs in a thread the request did not start
    conn.execute("PRAGMA journal_mode = WAL")
    applied = conn.execute("PRAGMA user_version").fetchone()[0]
    with conn:
        for n, migration in enumerate(MIGRATIONS[applied:], applied + 1):
            conn.executescript(migration)
            conn.execute(f"PRAGMA user_version = {n}")
        present = {row[1] for row in conn.execute("PRAGMA table_info(turns)")}
        for column, kind in TURN_COLUMNS.items():
            if column not in present:
                conn.execute(f"ALTER TABLE turns ADD COLUMN {column} {kind}")
    if seeds_from is not None:
        seed(conn, seeds_from)
    return conn


def seed(conn: sqlite3.Connection, directory: Path) -> None:
    """Import databases.yaml, users.yaml and the old sessions.db when present and not yet imported at this content."""
    for name, importer in (("databases.yaml", _import_databases), ("users.yaml", _import_users), ("../sessions.db", _import_sessions)):
        file = directory / name
        if not file.exists():
            continue
        sha = "once" if name.endswith(".db") else hashlib.sha1(file.read_bytes()).hexdigest()
        if conn.execute("SELECT 1 FROM seeds WHERE file = ? AND sha = ?", (name, sha)).fetchone():
            continue
        with conn:
            importer(conn, file)
            conn.execute("INSERT OR REPLACE INTO seeds VALUES (?, ?)", (name, sha))
            record(conn, "system", f"import {file.name}")


def _import_databases(conn: sqlite3.Connection, file: Path) -> None:
    entries = (yaml.safe_load(file.read_text()) or {}).get("databases", [])
    conn.execute("DELETE FROM databases")
    conn.executemany("INSERT INTO databases VALUES (:name, :url, :description, :schema_document)",
                     [{"description": "", **e} for e in entries])


def _import_users(conn: sqlite3.Connection, file: Path) -> None:
    users = (yaml.safe_load(file.read_text()) or {}).get("users", [])
    conn.execute("DELETE FROM grants")
    conn.execute("DELETE FROM users")
    conn.executemany("INSERT INTO users VALUES (?, ?)", [(u["name"], u.get("role", "analyst")) for u in users])
    conn.executemany("INSERT INTO grants VALUES (?, ?, 'seed', ?)",  # an admin holds everything by role, so a listed grant is noise
                     [(u["name"], db, now()) for u in users if u.get("role", "analyst") != "admin" for db in u.get("grants", [])])


def _import_sessions(conn: sqlite3.Connection, file: Path) -> None:
    """Adopt the Sessions the old store held, keeping ids so a `--session` someone wrote down still works."""
    conn.execute("ATTACH DATABASE ? AS old", (str(file),))
    try:
        conn.execute("INSERT OR IGNORE INTO sessions SELECT id, user, database, created_at, turns, messages FROM old.sessions")
        columns = [c for c in ("session_id", "n", "created_at", "question", "result_kind", "result", "metrics", "chart_spec")
                   if c in {row[1] for row in conn.execute("PRAGMA old.table_info(turns)")}]
        conn.execute(f"INSERT OR IGNORE INTO turns ({', '.join(columns)}) SELECT {', '.join(columns)} FROM old.turns")
    finally:
        conn.commit()
        conn.execute("DETACH DATABASE old")


def record(conn: sqlite3.Connection, actor: str, action: str, user: str | None = None, database: str | None = None) -> None:
    """One line of the audit trail: who did what to whom, when. Written inside the caller's transaction."""
    conn.execute("INSERT INTO audit (at, actor, action, user, database) VALUES (?, ?, ?, ?, ?)", (now(), actor, action, user, database))


def now() -> str:
    return datetime.now(UTC).isoformat()
