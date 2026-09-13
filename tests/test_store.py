"""The System Store: migrations, the seed files imported and re-imported, the old sessions.db adopted, the audit trail."""

import sqlite3

import yaml

from data_agents.contracts import AnalysisResult, QueryResult, TurnMetrics
from data_agents.data import registry
from data_agents.system import auth, store
from data_agents.system.sessions import SessionStore

OLD_SCHEMA = """
CREATE TABLE sessions (id TEXT PRIMARY KEY, user TEXT NOT NULL, database TEXT NOT NULL, created_at TEXT NOT NULL, turns INTEGER NOT NULL DEFAULT 0, messages BLOB);
CREATE TABLE turns (session_id TEXT NOT NULL REFERENCES sessions(id), n INTEGER NOT NULL, created_at TEXT NOT NULL, question TEXT NOT NULL,
                    result_kind TEXT NOT NULL, result TEXT NOT NULL, metrics TEXT NOT NULL, PRIMARY KEY (session_id, n));
INSERT INTO sessions VALUES ('abcd1234', 'alice', 'sakila', '2026-09-11T10:00:00+00:00', 1, NULL);
INSERT INTO turns VALUES ('abcd1234', 1, '2026-09-11T10:00:01+00:00', 'How many films?', 'Unanswerable', '{"reason": "none"}', '{"latency_ms": {}}');
"""


def old_sessions_db(path):
    with sqlite3.connect(path) as conn:
        conn.executescript(OLD_SCHEMA)
    return path


def test_migrations_run_once_and_add_the_turn_columns(tmp_path):
    conn = store.connect(tmp_path / "x.db")
    conn.close()
    conn = store.connect(tmp_path / "x.db")
    assert conn.execute("PRAGMA user_version").fetchone()[0] == len(store.MIGRATIONS)
    assert {"chart_spec", "vega_lite", "renderer"} <= {row[1] for row in conn.execute("PRAGMA table_info(turns)")}
    assert {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")} >= {"users", "grants", "databases", "sessions", "turns", "audit"}


def test_an_old_sessions_db_opened_as_a_store_migrates_in_place(tmp_path):
    """The benchmark's bench-sessions.db and a test's sessions.db predate the store; they keep working, rows intact."""
    sessions = SessionStore(old_sessions_db(tmp_path / "bench-sessions.db"))
    assert sessions.get("abcd1234").turns == 1 and sessions.turns_of("abcd1234")[0].question == "How many films?"
    assert sessions.list_sessions("alice")[0].id == "abcd1234"


def test_the_seed_files_import_and_the_old_sessions_db_beside_the_registry_is_adopted_once(system_store):
    old_sessions_db(system_store.parent / "sessions.db")
    databases, users = yaml.safe_load((system_store / "databases.yaml").read_text()), yaml.safe_load((system_store / "users.yaml").read_text())
    assert list(registry.load()) == [d["name"] for d in databases["databases"]]
    assert {name: u.grants for name, u in auth.load_users().items()} == {u["name"]: sorted(u.get("grants", [])) for u in users["users"]}
    assert auth.load_users()["admin"].role == "admin" and auth.effective_databases(auth.load_users()["admin"], registry.load()) == list(registry.load())
    sessions = SessionStore()
    assert sessions.get("abcd1234").user == "alice" and sessions.turns_of("abcd1234")[0].result.reason == "none"  # ids survive, so --session still works
    sessions.record_turn(sessions.get("abcd1234"), "and now?", [], AnalysisResult(intent="comparison", sql="SELECT 1", narrative="n", table=QueryResult(columns=["a"], rows=[[1]])), TurnMetrics(latency_ms={}))
    assert SessionStore().get("abcd1234").turns == 2  # a second open does not import the old file again
    assert {e.action for e in auth.audit()} >= {"import databases.yaml", "import users.yaml", "import sessions.db"}


def test_an_edited_seed_file_is_imported_again_and_an_untouched_one_leaves_the_store_alone(system_store):
    auth.grant("bob", "sakila", by="admin")
    assert auth.load_users()["bob"].grants == ["northwind_small", "sakila"]  # the store is the truth while the file stands still
    users = yaml.safe_load((system_store / "users.yaml").read_text())
    users["users"].append({"name": "carol", "role": "analyst", "grants": ["chinook"]})
    (system_store / "users.yaml").write_text(yaml.safe_dump(users))
    loaded = auth.load_users()
    assert loaded["carol"].grants == ["chinook"] and loaded["bob"].grants == ["northwind_small"]  # the file, once edited, is the truth again


def test_a_registry_saved_whole_then_a_users_file_written_is_the_benchmarks_flow(tmp_path):
    """tests/bench/viseval.register writes the Registry through `save` and users.yaml by hand, then the runner reads both."""
    bench = tmp_path / "bench-registry"
    bench.mkdir()
    registry.save({"t": registry.DatabaseEntry(name="t", url="sqlite:///t.db", schema_document="schemas/t.md")}, bench)
    (bench / "users.yaml").write_text('{"users": [{"name": "admin", "role": "admin", "grants": ["t"]}]}')
    admin = auth.load_users(bench)["admin"]
    assert list(registry.load(bench)) == ["t"] and admin.grants == [] and auth.effective_databases(admin, registry.load(bench)) == ["t"]  # by role
    registry.save({"u": registry.DatabaseEntry(name="u", url="sqlite:///u.db", schema_document="schemas/u.md")}, bench)
    (bench / "users.yaml").write_text('{"users": [{"name": "admin", "role": "admin", "grants": ["u"]}]}')
    assert auth.effective_databases(auth.load_users(bench)["admin"], registry.load(bench)) == ["u"]  # a second run with other cases


def test_every_change_is_a_line_in_the_audit_trail(system_store):
    auth.create_user("carol", "analyst", by="admin")
    auth.grant("carol", "sakila", by="admin")
    auth.grant("carol", "sakila", by="admin")  # already held: nothing happened, nothing recorded
    auth.revoke("carol", "sakila", by="admin")
    auth.remove_user("carol", by="admin")
    trail = [(e.actor, e.action, e.user, e.database) for e in auth.audit(4)]
    assert trail == [("admin", "remove user", "carol", None), ("admin", "revoke", "carol", "sakila"),
                     ("admin", "grant", "carol", "sakila"), ("admin", "create user", "carol", None)]
