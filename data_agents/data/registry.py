"""The Registry: every Database the system knows, in the System Store, each with its cached Schema Document on disk."""

from contextlib import closing
from pathlib import Path

from pydantic import BaseModel

from data_agents.data import schema_document
from data_agents.system import auth, store
from data_agents.data.database import ReadOnlyDatabase


class DatabaseEntry(BaseModel):
    name: str
    url: str  # SQLAlchemy-style URL; only sqlite:/// is opened tonight
    description: str = ""
    schema_document: str  # path relative to the registry directory


def _open(registry_dir: Path | None) -> closing:
    return closing(store.connect(registry_dir=registry_dir))


def load(registry_dir: Path | None = None) -> dict[str, DatabaseEntry]:
    with _open(registry_dir) as conn:
        rows = conn.execute("SELECT name, url, description, schema_document FROM databases ORDER BY rowid").fetchall()
        return {r[0]: DatabaseEntry(name=r[0], url=r[1], description=r[2], schema_document=r[3]) for r in rows}


def save(entries: dict[str, DatabaseEntry], registry_dir: Path | None = None) -> None:
    """Replace the whole Registry: how the benchmark builds one of its own."""
    with _open(registry_dir) as conn, conn:
        conn.execute("DELETE FROM databases")
        conn.executemany("INSERT INTO databases VALUES (:name, :url, :description, :schema_document)", [e.model_dump() for e in entries.values()])


def sqlite_path(url: str) -> Path:
    if not url.startswith("sqlite:///"):
        raise ValueError(f"only sqlite:/// URLs are supported tonight, got {url}")
    path = Path(url.removeprefix("sqlite:///"))
    if not path.is_file():  # the driver would say "unable to open database file" in an exception no surface translates
        raise FileNotFoundError(f"no database file at {path}")
    return path


def open_database(entry: DatabaseEntry) -> ReadOnlyDatabase:
    return ReadOnlyDatabase(sqlite_path(entry.url))


def read_schema_document(entry: DatabaseEntry, registry_dir: Path | None = None) -> str:
    return ((registry_dir or store.DIR) / entry.schema_document).read_text()


SQLITE_SUFFIXES = (".db", ".sqlite", ".sqlite3")


def unregistered_files(registry_dir: Path | None = None) -> list[Path]:
    """SQLite files sitting in the registry's data directory that no entry points at: what an admin can add in one click."""
    registered = {sqlite_path(e.url).resolve() for e in load(registry_dir).values()}
    found = (p for p in sorted(((registry_dir or store.DIR) / "data").glob("*")) if p.suffix in SQLITE_SUFFIXES)
    return [p for p in found if p.resolve() not in registered]


def register(name: str, url: str, description: str = "", document: str | None = None,
             registry_dir: Path | None = None, by: str = "system") -> DatabaseEntry:
    """Introspect (and draft a document unless one is given), verify, then write. Any failure writes nothing."""
    auth.check_name(name, "database")  # the name becomes schemas/<name>.md and a URL segment; nothing may walk out of either
    if name in load(registry_dir):
        raise ValueError(f"{name} is already registered")
    db = open_database(DatabaseEntry(name=name, url=url, schema_document=""))
    try:
        if document is None:
            document = schema_document.build(name, description, db)
        mismatches = schema_document.check_round_trip(document, db)
        if mismatches:
            raise ValueError("schema document does not match the database: " + "; ".join(mismatches))
    finally:
        db.close()
    directory = registry_dir or store.DIR
    (directory / "schemas").mkdir(exist_ok=True)
    (directory / "schemas" / f"{name}.md").write_text(document)
    entry = DatabaseEntry(name=name, url=url, description=description, schema_document=f"schemas/{name}.md")
    with _open(registry_dir) as conn, conn:
        conn.execute("INSERT INTO databases VALUES (:name, :url, :description, :schema_document)", entry.model_dump())
        store.record(conn, by, "register", database=name)
    return entry


def unregister(name: str, registry_dir: Path | None = None, by: str = "system") -> None:
    with _open(registry_dir) as conn, conn:
        if conn.execute("DELETE FROM databases WHERE name = ?", (name,)).rowcount == 0:
            raise KeyError(f"{name} is not registered")
        store.record(conn, by, "unregister", database=name)
    auth.revoke_everyone(name, by, registry_dir)  # a Grant is effective only while its Database is registered; now it is gone too
