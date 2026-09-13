import pytest
import yaml

from contextlib import closing

from data_agents.system import auth, store

USERS = {"users": [{"name": "alice", "grants": ["sakila", "chinook"]}, {"name": "bob", "grants": ["northwind_small"]}, {"name": "root", "role": "admin"}]}


@pytest.fixture
def registry_dir(tmp_path, monkeypatch):
    (tmp_path / "users.yaml").write_text(yaml.safe_dump(USERS))  # the seed file, imported on first open
    monkeypatch.setenv("DATA_AGENTS_TOKEN_ALICE", "a-token")
    monkeypatch.setenv("DATA_AGENTS_TOKEN_BOB", "b-token")
    return tmp_path


LIVE = ["sakila", "chinook", "northwind_small"]


def test_token_maps_to_user(registry_dir):
    assert auth.authenticate("b-token", registry_dir).name == "bob"


@pytest.mark.parametrize("token", [None, "", "wrong", "a-token "])
def test_bad_token_refused(registry_dir, token):
    with pytest.raises(auth.NotAuthenticated):
        auth.authenticate(token, registry_dir)


def test_user_without_token_variable_cannot_authenticate(registry_dir):
    with pytest.raises(auth.NotAuthenticated):
        auth.authenticate("", registry_dir)  # root has no token in the environment


def test_refused_before_any_agent_exists(registry_dir):
    bob = auth.authenticate("b-token", registry_dir)
    with pytest.raises(auth.NotAuthorized):
        auth.authorize(bob, "chinook", LIVE)


def test_effective_set_is_grants_intersect_registry(registry_dir):
    alice = auth.load_users(registry_dir)["alice"]
    assert auth.effective_databases(alice, LIVE) == ["chinook", "sakila"]  # a user's grants are listed alphabetically
    assert auth.effective_databases(alice, ["chinook"]) == ["chinook"]


def test_removed_database_leaves_effective_set(registry_dir):
    alice = auth.load_users(registry_dir)["alice"]
    auth.authorize(alice, "sakila", LIVE)
    with pytest.raises(auth.NotAuthorized):
        auth.authorize(alice, "sakila", ["chinook", "northwind_small"])


def test_grant_and_revoke(registry_dir):
    auth.grant("bob", "sakila", by="root", registry_dir=registry_dir)
    assert auth.load_users(registry_dir)["bob"].grants == ["northwind_small", "sakila"]
    auth.revoke("bob", "northwind_small", by="root", registry_dir=registry_dir)
    assert auth.load_users(registry_dir)["bob"].grants == ["sakila"]
    assert auth.grantees("sakila", registry_dir) == ["alice", "bob"]


def test_a_grant_to_nobody_says_so(registry_dir):
    with pytest.raises(KeyError, match="no user nosuchuser"):
        auth.grant("nosuchuser", "sakila", by="root", registry_dir=registry_dir)
    with pytest.raises(KeyError, match="no user nosuchuser"):
        auth.user("nosuchuser", registry_dir)


def test_users_are_created_and_removed(registry_dir):
    created = auth.create_user("carol", "analyst", by="root", registry_dir=registry_dir)
    assert created.role == "analyst" and auth.user("carol", registry_dir).grants == []
    for bad in ("carol", "c a r o l"):
        with pytest.raises(ValueError):
            auth.create_user(bad, "analyst", by="root", registry_dir=registry_dir)
    with pytest.raises(ValueError):
        auth.create_user("dave", "owner", by="root", registry_dir=registry_dir)  # not a role the system has
    auth.grant("carol", "sakila", by="root", registry_dir=registry_dir)
    auth.remove_user("carol", by="root", registry_dir=registry_dir)
    assert "carol" not in auth.load_users(registry_dir) and auth.grantees("sakila", registry_dir) == ["alice"]  # the grants went with her
    with pytest.raises(KeyError):
        auth.remove_user("carol", by="root", registry_dir=registry_dir)


def test_the_user_list_is_searched_and_paged(registry_dir):
    for i in range(25):
        auth.create_user(f"user{i:02d}", "analyst", by="root", registry_dir=registry_dir)
    first, total = auth.list_users("user", 1, 10, registry_dir)
    third, _ = auth.list_users("user", 3, 10, registry_dir)
    assert total == 25 and [u.name for u in first] == [f"user{i:02d}" for i in range(10)] and len(third) == 5
    found, total = auth.list_users("bo", registry_dir=registry_dir)
    assert total == 1 and found[0].name == "bob" and found[0].grants == ["northwind_small"]


def test_an_admin_holds_the_live_registry_by_role(registry_dir):
    root = auth.load_users(registry_dir)["root"]
    assert root.grants == [] and auth.effective_databases(root, LIVE) == LIVE
    auth.authorize(root, "sakila", LIVE)
    with pytest.raises(ValueError, match="holds every database"):
        auth.grant("root", "sakila", by="root", registry_dir=registry_dir)


def test_admin_required(registry_dir):
    users = auth.load_users(registry_dir)
    auth.require_admin(users["root"])
    with pytest.raises(auth.NotAuthorized):
        auth.require_admin(users["alice"])


def test_a_sign_in_expires_and_is_never_the_token(monkeypatch, system_store):
    from datetime import UTC, datetime, timedelta
    monkeypatch.setenv("DATA_AGENTS_TOKEN_ALICE", "alice-token")
    sign_in_id = auth.sign_in("alice-token")
    assert sign_in_id != "alice-token" and auth.signed_in_user(sign_in_id).name == "alice"
    with pytest.raises(auth.NotAuthenticated):
        auth.sign_in("nobody")
    with pytest.raises(auth.NotAuthenticated):
        auth.signed_in_user("made-up")
    with closing(store.connect(registry_dir=system_store)) as conn, conn:
        conn.execute("UPDATE sign_ins SET expires_at = ?", ((datetime.now(UTC) - timedelta(seconds=1)).isoformat(),))
    with pytest.raises(auth.NotAuthenticated):
        auth.signed_in_user(sign_in_id)


def test_a_sign_in_ends_with_a_sign_out_and_with_the_user(monkeypatch, system_store):
    """The other two ways a Sign-in row goes: the browser signs out, or the User is removed under it."""
    monkeypatch.setenv("DATA_AGENTS_TOKEN_ALICE", "alice-token")
    signed_out = auth.sign_in("alice-token")
    auth.sign_out(signed_out)
    with pytest.raises(auth.NotAuthenticated):
        auth.signed_in_user(signed_out)
    open_elsewhere = auth.sign_in("alice-token")
    auth.remove_user("alice", by="root")
    with pytest.raises(auth.NotAuthenticated):
        auth.signed_in_user(open_elsewhere)


@pytest.mark.parametrize("bad", ["../evil", "a b", "x/y", "", "dot.name"])
def test_a_database_or_user_name_stays_inside_its_file_and_url(bad):
    with pytest.raises(ValueError):
        auth.check_name(bad, "database")


def test_a_token_that_is_not_ascii_is_refused_rather_than_raising(registry_dir):
    """The constant-time comparison only takes ASCII text; a typed token can be anything, so it is compared as bytes."""
    with pytest.raises(auth.NotAuthenticated):
        auth.authenticate("tökén", registry_dir)
