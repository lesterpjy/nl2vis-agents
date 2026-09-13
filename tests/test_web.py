"""The GUI: the cookie, the pages a user may see, the stored Turn as a card, the stream the browser draws, the admin drawer.

No replay tapes here. Both agents are driven by FunctionModels, so the GUI's tests cannot go stale when a tape is re-recorded.
"""

import json
import re

import pytest
from fastapi.testclient import TestClient
from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart
from pydantic_ai.models.function import FunctionModel

from data_agents import orchestrator
from data_agents.agents import analysis, suggestion, visualization
from data_agents.charts import house_style, renderer
from data_agents.contracts import AnalysisResult, ChartSpec, Clarification, QueryResult, TurnMetrics, Unanswerable
from data_agents.web import gui, service
from data_agents.system.sessions import SessionStore, StoredChart

RATINGS = AnalysisResult(intent="comparison", sql="SELECT rating, COUNT(*) AS film_count FROM film GROUP BY rating",
                         narrative="PG-13 has the most films.", table=QueryResult(columns=["rating", "film_count"], rows=[["PG-13", 223], ["NC-17", 210]]))


@pytest.fixture
def client(tmp_path, monkeypatch):
    for name in ("alice", "bob", "admin"):
        monkeypatch.setenv(f"DATA_AGENTS_TOKEN_{name.upper()}", f"{name}-token")
    store = SessionStore(tmp_path / "sessions.db")
    service.app.dependency_overrides[gui.get_store] = lambda: store
    client = TestClient(service.app)
    client.store = store
    yield client
    service.app.dependency_overrides.clear()


def signed_in_as(client: TestClient, name: str) -> TestClient:
    response = client.post("/sign-in", data={"token": f"{name}-token"}, follow_redirects=False)
    assert response.status_code == 303 and response.headers["location"] == "/workspace"
    return client


def start(client: TestClient, name: str, database: str) -> str:
    signed_in_as(client, name)
    response = client.post("/workspace/sessions", data={"database": database}, follow_redirects=False)
    assert response.status_code == 303
    return response.headers["location"].rsplit("/", 1)[1]


def record(client: TestClient, session_id: str, question: str, result, spec: ChartSpec | None = None, stamp: str = renderer.FINGERPRINT) -> None:
    """A Turn in the store without a model: the history a reopened Session renders."""
    session = client.store.get(session_id)
    chart = StoredChart(spec=spec, vega_lite=renderer.build(spec, result, session.database), renderer=stamp) if spec else None
    client.store.record_turn(session, question, [], result, TurnMetrics(latency_ms={}), chart)


def chart_spec(**fields):
    return visualization.agent.override(model=FunctionModel(
        lambda messages, info: ModelResponse(parts=[ToolCallPart("final_result_ChartSpec", {"chart_type": "bar", "title": "Films by rating", **fields})])))


def answer(sql: str, intent: str = "comparison"):
    return analysis.agent.override(model=FunctionModel(
        lambda messages, info: ModelResponse(parts=[ToolCallPart("final_result_finish_analysis", {"intent": intent, "sql": sql, "narrative": "Here it is."})])))


def events(text: str) -> list[dict]:
    return [json.loads(block.split("\ndata: ", 1)[1]) for block in text.strip().split("\n\n") if block]


def test_a_page_without_a_cookie_redirects_to_sign_in(client):
    for path in ("/workspace", "/workspace/sessions/whatever", "/drawer"):
        response = client.get(path, follow_redirects=False)
        assert response.status_code == 303 and response.headers["location"] == "/"
    assert "Sign in" in client.get("/").text


def test_a_token_belonging_to_nobody_is_refused(client):
    assert "belongs to nobody" in client.post("/sign-in", data={"token": "not-a-token"}).text
    assert gui.COOKIE not in client.cookies


def test_signing_in_keeps_the_token_out_of_the_page_and_out_of_the_cookie(client):
    signed_in_as(client, "alice")
    assert client.cookies[gui.COOKIE] != "alice-token" and len(client.cookies[gui.COOKIE]) > 32  # a Sign-in id, not the credential
    page = client.get("/workspace").text
    assert "alice-token" not in page and "alice" in page
    client.post("/sign-out", follow_redirects=False)
    assert client.get("/workspace", follow_redirects=False).status_code == 303


def test_the_cookie_is_httponly_strict_and_secure_over_tls(client):
    response = client.post("/sign-in", data={"token": "alice-token"}, follow_redirects=False)
    cookie = response.headers["set-cookie"].lower()
    assert "httponly" in cookie and "samesite=strict" in cookie and "max-age=" in cookie and "secure" not in cookie  # plain http: no Secure
    tls = TestClient(service.app, base_url="https://testserver")
    assert "secure" in tls.post("/sign-in", data={"token": "alice-token"}, follow_redirects=False).headers["set-cookie"].lower()


def test_signing_out_kills_the_sign_in_even_for_a_copied_cookie(client):
    signed_in_as(client, "alice")
    copied = client.cookies[gui.COOKIE]
    client.post("/sign-out", follow_redirects=False)
    thief = TestClient(service.app, cookies={gui.COOKIE: copied})
    assert thief.get("/workspace", follow_redirects=False).status_code == 303


def test_a_removed_user_is_signed_out_everywhere(client):
    from data_agents.system import auth
    auth.create_user("carol", "analyst", by="test")
    with pytest.MonkeyPatch.context() as env:
        env.setenv("DATA_AGENTS_TOKEN_CAROL", "carol-token")
        signed_in_as(client, "carol")
        assert client.get("/workspace").status_code == 200
        auth.remove_user("carol", by="test")
        assert client.get("/workspace", follow_redirects=False).status_code == 303


def test_a_cross_site_post_is_refused_and_a_same_origin_one_is_not(client):
    signed_in_as(client, "alice")
    cross = client.post("/workspace/sessions", data={"database": "sakila"}, headers={"Sec-Fetch-Site": "cross-site"}, follow_redirects=False)
    assert cross.status_code == 403
    by_origin = client.post("/workspace/sessions", data={"database": "sakila"}, headers={"Origin": "https://evil.example"}, follow_redirects=False)
    assert by_origin.status_code == 403
    same = client.post("/workspace/sessions", data={"database": "sakila"}, headers={"Sec-Fetch-Site": "same-origin"}, follow_redirects=False)
    assert same.status_code == 303
    assert client.post("/sign-in", data={"token": "bob-token"}, headers={"Sec-Fetch-Site": "cross-site"}).status_code == 403  # login CSRF too


def test_a_session_row_can_be_deleted_from_the_workspace_and_stays_deleted(client):
    signed_in_as(client, "alice")
    session = client.store.create("alice", "sakila")
    client.store.record_turn(session, "how many films?", None, RATINGS, TurnMetrics(latency_ms={}))
    assert session.id in client.get("/workspace").text
    assert client.post(f"/workspace/sessions/{session.id}/delete").text == ""
    assert session.id not in client.get("/workspace").text
    assert client.get(f"/workspace/sessions/{session.id}").status_code == 404
    assert client.store.turns_of(session.id) == []


def test_the_workspace_offers_grants_intersect_registry(client):
    signed_in_as(client, "bob")
    page = client.get("/workspace").text
    assert 'value="northwind_small"' in page and "sakila" not in page


def test_a_session_belongs_to_the_user_who_started_it(client):
    session_id = start(client, "alice", "sakila")
    assert "sakila" in client.get(f"/workspace/sessions/{session_id}").text
    signed_in_as(client, "bob")
    assert client.get(f"/workspace/sessions/{session_id}").status_code == 403
    assert client.post(f"/workspace/sessions/{session_id}/turns", json={"question": "anything"}).status_code == 403


def test_starting_a_session_on_a_database_that_is_not_yours_says_so(client):
    signed_in_as(client, "bob")
    refused = client.post("/workspace/sessions", data={"database": "sakila"})
    assert refused.status_code == 200 and "no longer yours to open" in refused.text  # a stale page, not a bare 403
    assert client.store.list_sessions("bob") == []


def test_the_session_page_renders_every_stored_turn(client):
    session_id = start(client, "alice", "sakila")
    record(client, session_id, "How are films distributed across ratings?", RATINGS)
    record(client, session_id, "and by category?", Clarification(question="Which categories do you mean?"))
    record(client, session_id, "which director has the most films?", Unanswerable(reason="Sakila holds no directors."))
    page = client.get(f"/workspace/sessions/{session_id}").text
    assert page.count('class="turn"') == 3
    assert "PG-13 has the most films." in page and "GROUP BY rating" in page and "<td>223</td>" in page
    assert "Which categories do you mean?" in page and "Sakila holds no directors." in page


def test_one_turn_as_a_fragment_is_the_cards_own_markup(client):
    session_id = start(client, "alice", "sakila")
    record(client, session_id, "How are films distributed across ratings?", RATINGS)
    fragment = client.get(f"/workspace/sessions/{session_id}/turns/1")
    assert fragment.status_code == 200 and '<html' not in fragment.text and 'id="turn-1"' in fragment.text
    assert client.get(f"/workspace/sessions/{session_id}/turns/2").status_code == 404


def test_a_turn_streams_its_events_and_keeps_the_chart_spec_it_drew(client):
    session_id = start(client, "alice", "sakila")
    with answer("SELECT rating, AVG(rental_rate) AS rate FROM film GROUP BY rating"), chart_spec(x="rating", y="rate"):
        response = client.post(f"/workspace/sessions/{session_id}/turns", json={"question": "What does each rating rent for?", "chart": True})
    assert response.headers["content-type"].startswith("text/event-stream")
    stream = events(response.text)
    kinds = [event["kind"] for event in stream]
    assert kinds[0] == "stage_started" and "chart_ready" in kinds and kinds[-1] == "turn_finished"
    stored = client.store.turns_of(session_id)[0]
    assert stored.chart.spec.x == "rating" and stored.chart.renderer == renderer.FINGERPRINT  # kept by the orchestrator, with its Vega-Lite
    card = client.get(f"/workspace/sessions/{session_id}/turns/1").text
    assert "data-spec=" in card and house_style.SURFACE in card


def test_the_vendored_vega_lite_matches_the_schema_the_renderer_emits():
    """The browser draws the spec Altair wrote, so the bundle's major version must be the schema's; VENDORED.md is the record."""
    static = gui.HERE / "static"
    spec = renderer.build(ChartSpec(chart_type="bar", x="rating", y="film_count", title="t"), RATINGS, "sakila")
    major = spec["$schema"].split("/vega-lite/v")[1].split(".")[0]
    recorded = re.search(rf"\| `vega-lite\.min\.js` \| ({major}\.[\d.]+) \|", (static / "VENDORED.md").read_text())
    assert recorded, f"VENDORED.md records no vega-lite v{major} bundle, which the renderer now emits"
    assert f'"{recorded.group(1)}"' in (static / "vega-lite.min.js").read_text(errors="ignore")


def test_reopening_a_session_is_a_read(client, monkeypatch):
    """Twenty charted Turns, and neither the renderer nor a model is asked anything: the Vega-Lite each Turn kept is served."""
    session_id = start(client, "alice", "sakila")
    for n in range(20):
        record(client, session_id, f"question {n}", RATINGS, spec=ChartSpec(chart_type="bar", x="rating", y="film_count", title=f"Films by rating {n}"))
    monkeypatch.setattr(renderer, "build", lambda *a: pytest.fail("reopening a Session built a chart"))
    page = client.get(f"/workspace/sessions/{session_id}").text  # no agent override either: nothing here may call a model
    assert page.count("data-spec=") == 20 and "Films by rating 19" in page and house_style.SURFACE in page


def test_a_renderer_change_restyles_history_from_the_spec_and_keeps_the_result(client):
    """The Chart Spec is the record. A Turn drawn under another renderer is built again from it, once, and the new build is kept."""
    session_id = start(client, "alice", "sakila")
    record(client, session_id, "How are films distributed across ratings?", RATINGS,
           spec=ChartSpec(chart_type="bar", x="rating", y="film_count", title="Films by rating"), stamp="an-older-renderer")
    assert client.store.turns_of(session_id)[0].chart.renderer == "an-older-renderer"
    assert "Films by rating" in client.get(f"/workspace/sessions/{session_id}").text
    kept = client.store.turns_of(session_id)[0].chart
    assert kept.renderer == renderer.FINGERPRINT and kept.spec.title == "Films by rating"  # the spec did not change; its rendering did


def test_a_turn_that_cannot_finish_is_streamed_not_raised(client):
    session_id = start(client, "alice", "sakila")
    with answer("DELETE FROM film"):  # the guard refuses it every time, so the bounded retries run out
        stream = events(client.post(f"/workspace/sessions/{session_id}/turns", json={"question": "drop everything"}).text)
    assert stream[-1]["kind"] == "turn_error" and client.store.list_sessions("alice") == []


def test_a_session_nobody_asked_anything_in_is_not_history(client):
    start(client, "alice", "sakila")
    assert client.store.list_sessions("alice") == [] and "None yet" in client.get("/workspace").text


def suggests(text: str, prompts: list[str] | None = None):
    from pydantic_ai.models.function import FunctionModel

    def answer(messages, info):
        if prompts is not None:
            prompts.append(messages[-1].parts[-1].content)
        return ModelResponse(parts=[TextPart(text)])

    return suggestion.agent.override(model=FunctionModel(answer))


def test_a_clarification_is_answered_rather_than_changing_the_subject(client):
    session_id = start(client, "alice", "sakila")
    record(client, session_id, "Which categories sell best?", Clarification(question="Do you mean Drama or Documentary?"))
    prompts: list[str] = []
    with suggests("Drama", prompts):
        assert client.get(f"/workspace/sessions/{session_id}/suggestion").text == "Drama"
    assert "The analyst asked back: Do you mean Drama or Documentary?" in prompts[0]  # a reply, not a fresh question


def test_the_suggestion_follows_the_last_answer(client):
    session_id = start(client, "alice", "sakila")
    record(client, session_id, "How are films distributed across ratings?", RATINGS)
    prompts: list[str] = []
    with suggests("Which rating earns the most?", prompts):
        assert client.get(f"/workspace/sessions/{session_id}/suggestion").text == "Which rating earns the most?"
    assert "PG-13 has the most films." in prompts[0] and "rating, film_count" in prompts[0]


def test_history_is_never_redrawn_by_a_model(client):
    """The figure a user saw is the one they get back. A spec that was never kept cannot be reconstructed, only invented."""
    session_id = start(client, "alice", "sakila")
    record(client, session_id, "How are films distributed across ratings?", RATINGS)  # no spec, no image
    opened = client.get(f"/workspace/sessions/{session_id}")  # the key is bogus here, so any model call would raise
    assert opened.status_code == 200 and "data-spec=" not in opened.text and "<img" not in opened.text
    assert client.post(f"/workspace/sessions/{session_id}/turns/1/chart").status_code == 404  # nothing redraws history


def test_the_suggestion_box_survives_having_no_model(client):
    def broken(messages, info):
        raise RuntimeError("no model tonight")

    session_id = start(client, "alice", "sakila")
    with suggestion.agent.override(model=FunctionModel(broken)):
        suggested = client.get(f"/workspace/sessions/{session_id}/suggestion")
    assert suggested.status_code == 200 and suggested.text == ""


def test_a_suggestion_is_drafted_once_per_turn(client):
    session_id = start(client, "alice", "sakila")
    record(client, session_id, "How are films distributed across ratings?", RATINGS)
    prompts: list[str] = []
    with suggests("Which rating earns the most?", prompts):
        for _ in range(3):
            assert client.get(f"/workspace/sessions/{session_id}/suggestion").text == "Which rating earns the most?"
    assert len(prompts) == 1  # reopening a Session is a read, and the box is part of the Session


def test_the_session_page_says_what_the_database_can_answer(client):
    session_id = start(client, "alice", "sakila")
    page = client.get(f"/workspace/sessions/{session_id}").text
    assert "DVD rental store" in page and "<strong>film</strong>" in page and "rental_rate" in page


def test_the_cost_panel_belongs_to_the_session_page(client):
    signed_in_as(client, "admin")
    assert "This turn" not in client.get("/workspace").text  # nothing runs on the workspace, so it would always read empty
    session = client.post("/workspace/sessions", data={"database": "sakila"}, follow_redirects=False).headers["location"]
    assert "This turn" in client.get(session).text


def test_the_drawer_belongs_to_the_workspace_and_not_to_the_session_page(client):
    signed_in_as(client, "admin")
    assert 'hx-get="/drawer"' in client.get("/workspace").text
    session = client.post("/workspace/sessions", data={"database": "sakila"}, follow_redirects=False).headers["location"]
    assert 'hx-get="/drawer"' not in client.get(session).text


def test_a_token_that_is_not_ascii_belongs_to_nobody(client):
    response = client.post("/sign-in", data={"token": "tökén"}, follow_redirects=False)
    assert response.status_code == 200 and "belongs to nobody" in response.text


def test_the_first_question_is_remembered_per_schema_not_per_name(client):
    """A database registered again from another file under the same name is asked afresh; the schema is the key."""
    prompts: list[str] = []
    with suggests("Which film is longest?", prompts):
        assert suggestion.first_question("dbx", "film(title, length)") == suggestion.first_question("dbx", "film(title, length)")
        suggestion.first_question("dbx", "actor(name)")
    assert len(prompts) == 2
