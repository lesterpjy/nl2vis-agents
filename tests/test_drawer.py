"""The admin drawer: a role check, the Registry, users navigated from either side, grants by typeahead, the audit trail."""

import pytest
from fastapi.testclient import TestClient

from data_agents.system import auth
from data_agents.web import drawer, gui, service
from data_agents.system.sessions import SessionStore


@pytest.fixture
def admin(tmp_path, monkeypatch) -> TestClient:
    for name in ("alice", "bob", "admin"):
        monkeypatch.setenv(f"DATA_AGENTS_TOKEN_{name.upper()}", f"{name}-token")
    service.app.dependency_overrides[gui.get_store] = lambda: SessionStore(tmp_path / "sessions.db")
    client = TestClient(service.app)
    client.post("/sign-in", data={"token": "admin-token"}, follow_redirects=False)
    yield client
    service.app.dependency_overrides.clear()


def test_the_drawer_is_a_role_check(admin):
    admin.post("/sign-in", data={"token": "alice-token"}, follow_redirects=False)
    assert admin.get("/drawer").status_code == 403
    assert admin.post("/drawer/grants", data={"user_name": "bob", "database": "sakila"}).status_code == 403
    assert admin.get("/drawer/users").status_code == 403


def test_the_drawer_lists_the_registry_and_offers_users_by_page(admin):
    page = admin.get("/drawer").text
    assert "Registry" in page and "sakila" in page and 'hx-get="/drawer/users"' in page and 'hx-get="/drawer/audit"' in page
    users = admin.get("/drawer/users").text
    assert "alice" in users and "bob" in users and "page" not in users  # three users fit one page
    assert "all databases" in users and "no databases" not in users  # the admin's line
    for i in range(12):
        auth.create_user(f"user{i:02d}", "analyst", by="admin")
    paged = admin.get("/drawer/users", params={"q": "user"}).text
    assert "user09" in paged and "user10" not in paged and "page 1 of 2" in paged and "next ›" in paged
    assert "user10" in admin.get("/drawer/users", params={"q": "user", "p": 2}).text


def test_one_users_access_offers_only_what_they_lack_and_grants_by_typeahead(admin):
    focus = admin.get("/drawer/users/bob").text
    assert "northwind_small" in focus and '<option value="sakila">' in focus and '<option value="northwind_small">' not in focus
    granted = admin.post("/drawer/grants", data={"user_name": "bob", "database": "sakila"})
    assert granted.headers["HX-Trigger"] == "changed" and "HX-Refresh" not in granted.headers
    assert auth.user("bob").grants == ["northwind_small", "sakila"] and '<option value="sakila">' not in granted.text
    revoked = admin.post("/drawer/grants", data={"user_name": "bob", "database": "sakila", "revoke": "true"})
    assert auth.user("bob").grants == ["northwind_small"] and '<option value="sakila">' in revoked.text
    assert admin.post("/drawer/grants", data={"user_name": "nosuchuser", "database": "sakila"}).status_code == 404
    assert admin.post("/drawer/grants", data={"user_name": "bob", "database": "nope"}).status_code == 404


def test_a_database_opens_its_grantees_and_grants_from_that_side(admin):
    side = admin.get("/drawer/databases/chinook").text
    assert "1 grantee" in side and ">alice<" in side and '<option value="bob">' in side and '<option value="alice">' not in side
    assert "Every admin (admin) holds it by role" in side and '<option value="admin">' not in side and "revoke" not in side
    granted = admin.post("/drawer/grants", data={"user_name": "bob", "database": "chinook", "side": "database"})
    assert "2 grantees" in granted.text and auth.grantees("chinook") == ["alice", "bob"]
    assert admin.get("/drawer/databases/nope").status_code == 404


def test_an_admin_holds_every_database_by_role_and_is_never_granted(admin):
    focus = admin.get("/drawer/users/admin").text
    assert "Holds every registered database" in focus and 'name="revoke"' not in focus and "<datalist" not in focus
    assert admin.post("/drawer/grants", data={"user_name": "admin", "database": "chinook"}).status_code == 400
    assert [d["name"] for d in admin.get("/databases", headers={"Authorization": "Bearer admin-token"}).json()] == ["sakila", "chinook", "northwind_small"]


def test_users_are_created_and_removed_from_the_drawer(admin):
    created = admin.post("/drawer/users", data={"name": "carol", "role": "analyst"})
    assert created.status_code == 200 and "carol" in created.text and "holds no databases" in created.text
    assert "already exists" in admin.post("/drawer/users", data={"name": "carol"}).text
    assert admin.post("/drawer/users/carol/remove").status_code == 200 and "carol" not in auth.load_users()
    assert admin.post("/drawer/users/admin/remove").status_code == 400  # not yourself
    assert admin.post("/drawer/users/carol/remove").status_code == 404


def test_the_audit_trail_says_who_did_what(admin):
    admin.post("/drawer/grants", data={"user_name": "bob", "database": "sakila"})
    trail = admin.get("/drawer/audit").text
    assert "<td>admin</td><td>grant</td>" in trail and "bob · sakila" in trail


def test_only_a_file_the_registry_offered_may_be_registered(admin, monkeypatch, tmp_path):
    monkeypatch.setattr(drawer.registry, "unregistered_files", lambda: [tmp_path / "spare.db"])
    page = admin.get("/drawer").text
    assert "On disk, not registered" in page and "spare.db" in page
    refused = admin.post("/drawer/databases", data={"path": "/etc/passwd"})
    assert refused.status_code == 200 and "not one of the files on offer" in refused.text


def test_registering_a_database_that_cannot_be_opened_writes_nothing(admin):
    page = admin.post("/drawer/databases", data={"name": "nope", "url": "postgres://nope", "document": "# nope"})
    assert page.status_code == 200 and "only sqlite:/// URLs" in page.text
    assert "nope" not in [entry["name"] for entry in admin.get("/databases", headers={"Authorization": "Bearer admin-token"}).json()]


def test_unregistering_revokes_everyone_and_refreshes(admin):
    gone = admin.post("/drawer/databases/chinook/unregister")
    assert gone.headers["HX-Refresh"] == "true" and auth.user("alice").grants == ["sakila"] and auth.grantees("chinook") == []
    assert admin.post("/drawer/databases/chinook/unregister").status_code == 404
