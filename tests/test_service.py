"""The service over replay tapes: token to User, authorization before any model call, Turn events over SSE, admin endpoints."""

import json

import pytest
from fastapi.testclient import TestClient

from data_agents import orchestrator
from data_agents.web import service
from data_agents.system.sessions import SessionStore
from tests.evals.runner import TAPES
from tests.evals.tape import replaying


@pytest.fixture
def client(tmp_path, monkeypatch):
    for name in ("alice", "bob", "admin"):
        monkeypatch.setenv(f"DATA_AGENTS_TOKEN_{name.upper()}", f"{name}-token")
    store = SessionStore(tmp_path / "sessions.db")
    service.app.dependency_overrides[service.get_store] = lambda: store
    yield TestClient(service.app)
    service.app.dependency_overrides.clear()


def as_user(name: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {name}-token"}


def sse_events(text: str) -> list[dict]:
    return [json.loads(block.split("\ndata: ", 1)[1]) for block in text.strip().split("\n\n") if block]


def test_missing_or_wrong_token_is_401(client):
    assert client.get("/databases").status_code == 401
    assert client.get("/databases", headers={"Authorization": "Bearer nope"}).status_code == 401


def test_databases_are_grants_intersect_registry(client):
    assert [d["name"] for d in client.get("/databases", headers=as_user("bob")).json()] == ["northwind_small"]
    assert client.get("/me", headers=as_user("alice")).json()["grants"] == ["chinook", "sakila"]


def test_session_on_forbidden_database_is_403(client):
    assert client.post("/sessions", json={"database": "chinook"}, headers=as_user("bob")).status_code == 403


def test_turn_streams_events_and_persists(client):
    session = client.post("/sessions", json={"database": "sakila"}, headers=as_user("alice")).json()
    with replaying(TAPES / "sakila_category_revenue.json"):
        response = client.post(f"/sessions/{session['id']}/turns", json={"question": "How much revenue does each film category bring in?", "chart": True},
                               headers=as_user("alice"))
    assert response.headers["content-type"].startswith("text/event-stream")
    events = sse_events(response.text)
    kinds = [e["kind"] for e in events]
    assert kinds[0] == "stage_started" and "sql_written" in kinds and "chart_ready" in kinds and kinds[-1] == "turn_finished"
    finished = events[-1]
    assert len(finished["result"]["table"]["rows"]) == 16 and finished["metrics"]["input_tokens"] > 0
    assert client.get(f"/sessions/{session['id']}", headers=as_user("alice")).json()["turns"] == 1
    assert client.get(f"/sessions/{session['id']}", headers=as_user("bob")).status_code == 403


def test_turn_error_is_streamed_not_raised(client):
    session = client.post("/sessions", json={"database": "northwind_small"}, headers=as_user("bob")).json()
    events = sse_events(client.post(f"/sessions/{session['id']}/chart", headers=as_user("bob")).text)  # nothing to chart yet
    assert events == [{"kind": "turn_error", "stage": "service", "message": f"session {session['id']} has no analysis result yet", "detail": ""}]


def test_an_admin_reads_the_exception_and_an_analyst_the_sentence(client):
    session = client.post("/sessions", json={"database": "northwind_small"}, headers=as_user("admin")).json()
    [event] = sse_events(client.post(f"/sessions/{session['id']}/chart", headers=as_user("admin")).text)
    assert event["detail"] == f"'session {session['id']} has no analysis result yet'"  # the exception's own text, admins only


def test_a_session_is_deleted_with_its_turns_and_only_by_its_owner(client):
    session = client.post("/sessions", json={"database": "northwind_small"}, headers=as_user("bob")).json()
    assert client.delete(f"/sessions/{session['id']}", headers=as_user("alice")).status_code == 403
    assert client.delete(f"/sessions/{session['id']}", headers=as_user("bob")).status_code == 204
    assert client.get(f"/sessions/{session['id']}", headers=as_user("bob")).status_code == 404


def test_model_calling_routes_are_paced_per_user(client, monkeypatch):
    from data_agents.web import pacing
    monkeypatch.setattr(pacing, "PER_MINUTE", 2)
    session = client.post("/sessions", json={"database": "northwind_small"}, headers=as_user("bob")).json()
    codes = [client.post(f"/sessions/{session['id']}/chart", headers=as_user("bob")).status_code for _ in range(3)]
    assert codes == [200, 200, 429]
    assert client.post(f"/sessions/{session['id']}/chart", headers=as_user("bob")).headers["retry-after"] == "60"
    assert client.post("/sessions", json={"database": "sakila"}, headers=as_user("alice")).status_code == 201  # alice's minute is her own


def test_turns_in_flight_are_capped(client, monkeypatch):
    import threading
    from data_agents.web import pacing
    monkeypatch.setattr(pacing, "_slots", threading.BoundedSemaphore(1))
    pacing._slots.acquire()  # one Turn running elsewhere
    session = client.post("/sessions", json={"database": "northwind_small"}, headers=as_user("bob")).json()
    assert client.post(f"/sessions/{session['id']}/chart", headers=as_user("bob")).status_code == 429


def test_a_database_name_that_could_walk_out_of_the_registry_is_refused(client):
    response = client.post("/admin/databases", json={"name": "../evil", "url": "sqlite:///registry/data/sakila.db", "document": "x"}, headers=as_user("admin"))
    assert response.status_code == 400 and "letters, digits" in response.json()["detail"]


def test_every_response_refuses_mime_sniffing(client):
    assert client.get("/me", headers=as_user("bob")).headers["x-content-type-options"] == "nosniff"


def test_admin_endpoints_need_admin_role(client):
    assert client.post("/admin/grants", json={"user": "bob", "database": "sakila"}, headers=as_user("alice")).status_code == 403
    assert client.delete("/admin/databases/sakila", headers=as_user("bob")).status_code == 403


def test_grant_then_revoke_changes_effective_databases(client):
    try:
        assert client.post("/admin/grants", json={"user": "bob", "database": "sakila"}, headers=as_user("admin")).status_code == 204
        assert [d["name"] for d in client.get("/databases", headers=as_user("bob")).json()] == ["northwind_small", "sakila"]
    finally:
        assert client.delete("/admin/grants/bob/sakila", headers=as_user("admin")).status_code == 204
    assert [d["name"] for d in client.get("/databases", headers=as_user("bob")).json()] == ["northwind_small"]
    assert client.post("/admin/grants", json={"user": "bob", "database": "nope"}, headers=as_user("admin")).status_code == 404
    assert client.post("/admin/grants", json={"user": "nosuchuser", "database": "sakila"}, headers=as_user("admin")).status_code == 404


def test_users_are_created_listed_and_removed(client):
    created = client.post("/admin/users", json={"name": "carol", "grants": ["sakila"]}, headers=as_user("admin"))
    assert created.status_code == 201 and created.json() == {"name": "carol", "role": "analyst", "grants": ["sakila"]}
    assert client.post("/admin/users", json={"name": "carol"}, headers=as_user("admin")).status_code == 400
    assert client.post("/admin/users", json={"name": "dave", "grants": ["nope"]}, headers=as_user("admin")).status_code == 404
    assert [u["name"] for u in client.get("/admin/users", params={"q": "car"}, headers=as_user("admin")).json()] == ["carol"]
    assert client.delete("/admin/users/carol", headers=as_user("admin")).status_code == 204
    assert client.delete("/admin/users/carol", headers=as_user("admin")).status_code == 404
    trail = client.get("/admin/audit", params={"limit": 3}, headers=as_user("admin")).json()
    assert [(e["actor"], e["action"], e["user"]) for e in trail] == [("admin", "remove user", "carol"), ("admin", "grant", "carol"), ("admin", "create user", "carol")]
    assert client.get("/admin/audit", headers=as_user("alice")).status_code == 403


def chart(**fields):
    from pydantic_ai.messages import ModelResponse, ToolCallPart
    from pydantic_ai.models.function import FunctionModel
    from data_agents.agents.visualization import agent
    return agent.override(model=FunctionModel(lambda messages, info: ModelResponse(parts=[ToolCallPart("final_result_ChartSpec", {"chart_type": "bar", "title": "t", **fields})])))


def test_sessions_lists_only_mine(client):
    mine = client.post("/sessions", json={"database": "sakila"}, headers=as_user("alice")).json()
    client.post("/sessions", json={"database": "northwind_small"}, headers=as_user("bob"))
    assert client.get("/sessions", headers=as_user("alice")).json() == []  # a Session nobody asked anything in is not history
    with replaying(TAPES / "sakila_rating_distribution.json"):
        client.post(f"/sessions/{mine['id']}/turns", json={"question": "How are films distributed across ratings?"}, headers=as_user("alice"))
    listed = client.get("/sessions", headers=as_user("alice")).json()
    assert [s["database"] for s in listed] == ["sakila"] and listed[0]["turns"] == 1


def test_supplied_sql_is_charted_with_the_same_guard_and_authorization(client):
    sql = 'SELECT s.CompanyName, COUNT(*) AS orders FROM "Order" o JOIN Shipper s ON s.Id = o.ShipVia GROUP BY 1'
    assert client.post("/charts", json={"database": "northwind_small", "sql": sql}, headers=as_user("alice")).status_code == 403  # not hers
    assert client.post("/charts", json={"database": "northwind_small", "sql": 'DELETE FROM "Order"'}, headers=as_user("bob")).status_code == 400
    assert client.post("/charts", json={"database": "northwind_small"}, headers=as_user("bob")).status_code == 422
    with chart(x="CompanyName", y="orders"):
        events = sse_events(client.post("/charts", json={"database": "northwind_small", "sql": sql, "intent": "share"}, headers=as_user("bob")).text)
    assert [e["kind"] for e in events] == ["stage_started", "model_call_done", "chart_ready", "turn_finished"]
    assert events[-1]["result"]["sql"] == sql and events[-1]["charted"] is True


def test_supplied_csv_is_charted(client):
    with chart(x="month", y="rain"):
        events = sse_events(client.post("/charts", json={"csv": "month,rain\n2024-01,80\n2024-02,61\n", "name": "rain.csv", "intent": "trend"},
                                        headers=as_user("alice")).text)
    assert events[-1]["kind"] == "turn_finished" and events[-1]["result"]["table"]["rows"] == [["2024-01", 80], ["2024-02", 61]]


def test_turn_hints_reach_the_visualization_prompt(client):
    from pydantic_ai.messages import ModelResponse, ToolCallPart
    from pydantic_ai.models.function import FunctionModel
    from data_agents.agents.visualization import agent
    prompts = []

    def echo(messages, info):
        prompts.append(messages[-1].parts[-1].content)
        return ModelResponse(parts=[ToolCallPart("final_result_ChartSpec", {"chart_type": "bar", "x": "rating", "y": "film_count", "title": "t"})])

    session = client.post("/sessions", json={"database": "sakila"}, headers=as_user("alice")).json()
    with replaying(TAPES / "sakila_rating_distribution.json"), agent.override(model=FunctionModel(echo)):
        response = client.post(f"/sessions/{session['id']}/turns", headers=as_user("alice"),
                               json={"question": "How are films distributed across ratings?", "chart": True, "hints": {"chart_type": "bar", "title": "Films by rating"}})
    assert response.status_code == 200 and "User preferences: chart_type = 'bar', title = 'Films by rating'" in prompts[0]


def test_an_upload_name_is_a_caption_and_never_a_path(client, tmp_path):
    """The name an upload carries was once also a file stem, which a `../` could walk out of. Nothing derives a path from it
    now — no Turn writes a file at all — so the name reaches the caption and nowhere else."""
    with chart(x="month", y="rain"):
        events = sse_events(client.post("/charts", json={"csv": "month,rain\n2024-01,80\n2024-02,61\n", "name": "../../escape.csv"},
                                        headers=as_user("alice")).text)
    assert events[-1]["charted"] is True and not list(tmp_path.rglob("*.png"))


def test_registering_a_file_that_is_not_there_is_a_sentence(client):
    response = client.post("/admin/databases", json={"name": "ghost", "url": "sqlite:///registry/data/ghost.db", "document": "x"}, headers=as_user("admin"))
    assert response.status_code == 400 and "no database file" in response.json()["detail"]
