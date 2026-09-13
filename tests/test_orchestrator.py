"""Tier 3: the orchestrator, Session Store, Clarification path, and chart rules over replay tapes. No key needed."""

import pytest
from pydantic_ai.exceptions import ModelHTTPError, UnexpectedModelBehavior, UsageLimitExceeded
from pydantic_ai.messages import ModelResponse, ToolCallPart
from pydantic_ai.models.function import FunctionModel

from data_agents import orchestrator
from data_agents.charts import chartable
from data_agents.contracts import AnalysisResult, ChartHints, Clarification, QueryResult, TurnMetrics
from data_agents.data import supplied
from data_agents.system import auth
from data_agents.agents.analysis import LIMITS, agent as analysis_agent
from data_agents.agents.visualization import agent as visualization_agent, describe
from data_agents.system.sessions import SessionStore
from tests.evals.runner import TAPES
from tests.evals.tape import player, replaying


@pytest.fixture
def store(tmp_path, monkeypatch):
    return SessionStore(tmp_path / "sessions.db")


def turn(store, session, question, tape, chart=False):
    events = []
    with replaying(TAPES / f"{tape}.json"):
        result = orchestrator.run_turn(store, session, question, events.append, chart=chart)
    return result, events


def turns(store, session, questions, tape):
    """A multi-Turn case replays one tape across its Turns, as the live run consumed it."""
    with replaying(TAPES / f"{tape}.json"):
        return [(orchestrator.run_turn(store, session, q, lambda e: None), len(store.history(session.id))) for q in questions]


def kinds(events) -> list[str]:
    return [e.kind for e in events]


def test_turn_persists_history_and_result(store):
    session = store.create("alice", "sakila")
    result, events = turn(store, session, "How much revenue does each film category bring in?", "sakila_category_revenue")
    assert isinstance(result, AnalysisResult) and len(result.table.rows) == 16
    assert kinds(events)[:3] == ["stage_started", "stage_started", "sql_written"] and kinds(events)[-1] == "turn_finished"
    assert store.get(session.id).turns == 1 and len(store.history(session.id)) > 0
    assert store.last_result(session.id).sql == result.sql
    assert all(getattr(m, "instructions", None) is None for m in store.history(session.id))  # the Schema Document is not persisted
    assert events[-1].metrics.input_tokens > 0 and events[-1].metrics.cost_usd > 0  # from the tape's recorded usage


def test_a_charted_turn_keeps_its_spec_and_says_so(store):
    """A Turn writes no image: what it keeps is the Chart Spec and the Vega-Lite built from it, which every client draws."""
    session = store.create("alice", "sakila")
    _, events = turn(store, session, "How much revenue does each film category bring in?", "sakila_category_revenue", chart=True)
    ready = next(e for e in events if e.kind == "chart_ready")
    kept = store.last_chart(session.id)
    assert events[-1].charted and kept.spec == ready.spec and kept.vega_lite["data"]["values"]


def test_follow_up_turn_carries_history(store):
    session = store.create("alice", "chinook")
    (first, history_after_first), (result, history_after_second) = turns(store, session, ["How many tracks does each genre have?", "Only the top 3"],
                                                                         "chinook_top_genres_followup")
    assert len(first.table.rows) == 25 and store.get(session.id).turns == 2 and history_after_second > history_after_first
    assert isinstance(result, AnalysisResult) and len(result.table.rows) == 3


def test_clarification_ends_turn_and_answer_continues(store):
    session = store.create("alice", "chinook")
    events = []
    with replaying(TAPES / "chinook_compare_periods_clarifies.json"):
        asked = orchestrator.run_turn(store, session, "Compare sales between the two periods", events.append)
        assert isinstance(asked, Clarification) and "sql_written" not in kinds(events) and store.get(session.id).turns == 1
        with pytest.raises(KeyError):
            store.last_result(session.id)
        answer = orchestrator.run_turn(store, session, "2012 versus 2013, total invoice revenue", lambda e: None)
    assert isinstance(answer, AnalysisResult) and len(answer.table.rows) == 2 and store.get(session.id).turns == 2


def test_unauthorized_session_is_refused_before_any_model_call(store):
    session = store.create("bob", "chinook")
    events = []
    with analysis_agent.override(model=player([])), pytest.raises(auth.NotAuthorized):  # an empty tape: any model call would fail loudly
        orchestrator.run_turn(store, session, "anything", events.append)
    assert kinds(events) == ["stage_started", "turn_error"] and events[-1].stage == "authorize"
    assert store.get(session.id).turns == 0


def test_turn_cap(store):
    session = store.create("alice", "sakila")
    session.turns = orchestrator.MAX_TURNS_PER_SESSION
    with analysis_agent.override(model=player([])), pytest.raises(RuntimeError, match="turn cap"):
        orchestrator.run_turn(store, session, "anything", lambda e: None)


def test_single_value_is_printed_not_charted(store):
    session = store.create("alice", "sakila")
    result, events = turn(store, session, "How many films are rated R?", "sakila_r_rated_count", chart=True)
    skipped = [e for e in events if e.kind == "chart_skipped"]
    assert skipped and skipped[0].value == "195" and "chart_ready" not in kinds(events)
    assert not events[-1].charted and "visualization" not in events[-1].metrics.latency_ms


def test_trend_gets_a_temporal_axis(store):
    session = store.create("alice", "chinook")
    _, events = turn(store, session, "How did invoice revenue develop per year?", "chinook_revenue_per_year", chart=True)
    ready = next(e for e in events if e.kind == "chart_ready")
    spec = orchestrator.renderer.build(ready.spec, events[-1].result, "chinook")
    assert ready.spec.chart_type == "line" and spec["encoding"]["x"]["type"] == "temporal"
    assert spec["encoding"]["x"]["axis"] == {"format": "%Y", "tickCount": {"interval": "year", "step": 1}, "labelOverlap": False}  # one tick per year
    assert spec["data"]["format"]["parse"][ready.spec.x] == "date:'%Y'"
    assert events[-1].charted and store.last_chart(session.id) is not None


def test_multi_series_bar_is_grouped(store):
    session = store.create("bob", "northwind_small")
    _, events = turn(store, session, "Revenue per product category for each year", "northwind_small_category_revenue_by_year", chart=True)
    ready = next(e for e in events if e.kind == "chart_ready")
    spec = orchestrator.renderer.build(ready.spec, events[-1].result, "northwind_small")
    # The model chose which two columns split the bars; the renderer may swap them so eight categories are not eight colours.
    assert ready.spec.color and spec["encoding"]["color"]["field"] in (ready.spec.color, ready.spec.x)
    assert {"xOffset", "yOffset"} & spec["encoding"].keys()  # side by side, not stacked


def test_a_looping_model_is_cut_off_by_the_request_limit(store):
    """The request limit, not the retry count: retries bound validation failures, not a model that keeps calling a tool
    successfully."""
    session = store.create("alice", "sakila")
    forever = FunctionModel(lambda messages, info: ModelResponse(parts=[ToolCallPart("run_sql", {"sql": "SELECT 1"})]), model_name="looping")
    with analysis_agent.override(model=forever), pytest.raises(UsageLimitExceeded):
        orchestrator.run_turn(store, session, "anything", lambda e: None)
    assert store.get(session.id).turns == 0


def test_first_empty_answer_is_refused_second_is_accepted(store):
    """The value-linking enforcement layer: a zero-row final SQL comes back once as a retry, then stands if the model insists."""
    session = store.create("alice", "sakila")
    finish = {"intent": "comparison", "sql": "SELECT title FROM film WHERE title = 'nothing'", "narrative": "No film is called that."}
    seen: list[list] = []

    def insist(messages, info):
        seen.append([p.part_kind for p in messages[-1].parts])
        return ModelResponse(parts=[ToolCallPart("final_result_finish_analysis", finish)])

    with analysis_agent.override(model=FunctionModel(insist, model_name="insisting")):
        result = orchestrator.run_turn(store, session, "Films called nothing?", lambda e: None)
    assert isinstance(result, AnalysisResult) and result.table.rows == []
    assert len(seen) == 2 and seen[1] == ["retry-prompt"]  # exactly one refusal, carried back to the model as a retry


def finish(sql: str, intent: str = "distribution") -> ModelResponse:
    return ModelResponse(parts=[ToolCallPart("final_result_finish_analysis", {"intent": intent, "sql": sql, "narrative": "n"})])


def chart_spec(**fields) -> ModelResponse:
    return ModelResponse(parts=[ToolCallPart("final_result_ChartSpec", {"chart_type": "bar", "x": "rating", "y": "films", **fields})])


TOP_ACTORS = "Which five actors appear in the most films?"
SPLIT = ("SELECT first_name, last_name, COUNT(*) AS films FROM actor a JOIN film_actor fa ON fa.actor_id = a.actor_id "
         "GROUP BY a.actor_id ORDER BY films DESC LIMIT 5")
JOINED = ("SELECT first_name || ' ' || last_name AS actor, COUNT(*) AS films FROM actor a JOIN film_actor fa ON fa.actor_id = a.actor_id "
          "GROUP BY a.actor_id ORDER BY films DESC LIMIT 5")


def answering(*sqls: str, texts: list[str] | None = None):
    """One FunctionModel that finishes with each SQL in turn, and the request parts it was sent each time (their text in `texts`)."""
    seen: list[list] = []

    def answer(messages, info):
        seen.append([p.part_kind for p in messages[-1].parts])
        if texts is not None:
            texts.append(" ".join(str(getattr(p, "content", "")) for p in messages[-1].parts))
        return finish(sqls[min(len(seen), len(sqls)) - 1], intent="comparison")

    return seen, FunctionModel(answer, model_name="answering")


def test_a_split_row_identity_is_refused_once_then_charted(store):
    """The chartable-shape layer: two name columns cannot label one bar, so the first such answer comes back as a retry."""
    session = store.create("alice", "sakila")
    seen, answering_model = answering(SPLIT, JOINED)
    drawing = FunctionModel(lambda m, i: chart_spec(x="actor", y="films", title="Gina Degeneres leads"))
    with analysis_agent.override(model=answering_model), visualization_agent.override(model=drawing):
        result = orchestrator.run_turn(store, session, TOP_ACTORS, lambda e: None, chart=True)
    assert len(seen) == 2 and seen[1] == ["retry-prompt"]  # exactly one refusal, carried back to the model as a retry
    assert isinstance(result, AnalysisResult) and result.table.columns == ["actor", "films"]


def test_a_split_row_identity_that_the_model_repeats_is_escalated_then_accepted(store):
    """The ladder: the second refusal asks the model to put the decision to the user; a model that offers the same
    shape a third time still reaches the user with it, rather than erroring the Turn."""
    session = store.create("alice", "sakila")
    texts: list[str] = []
    seen, answering_model = answering(SPLIT, SPLIT, SPLIT, texts=texts)
    with analysis_agent.override(model=answering_model), visualization_agent.override(model=FunctionModel(lambda m, i: chart_spec(x="first_name", y="films", title="Gina leads"))):
        result = orchestrator.run_turn(store, session, TOP_ACTORS, lambda e: None, chart=True)
    assert len(seen) == 3 and result.table.columns == ["first_name", "last_name", "films"]
    assert "may not finish" not in texts[1] and "may not finish" in texts[2]


HALF_FIXED = ("SELECT c.first_name || ' ' || c.last_name AS customer, c.email, SUM(p.amount) AS spend FROM customer c "
              "JOIN payment p ON p.customer_id = c.customer_id GROUP BY c.customer_id ORDER BY spend DESC LIMIT 5")


def test_a_half_fixed_shape_is_accepted_and_still_charts_readably(store):
    """Seen live: the model combined the name and kept the email, which is also unique per row. The ladder is spent, so the
    shape stands; the renderer's colour rule is the guarantee that the chart reads anyway."""
    session = store.create("alice", "sakila")
    seen, answering_model = answering(SPLIT, HALF_FIXED, HALF_FIXED)
    drawing = FunctionModel(lambda m, i: chart_spec(x="customer", y="spend", color="email", title="Karl Seal spends the most"))
    events = []
    with analysis_agent.override(model=answering_model), visualization_agent.override(model=drawing):
        result = orchestrator.run_turn(store, session, "Which five customers have spent the most?", events.append, chart=True)
    assert len(seen) == 3 and chartable.split_identity(result.table) == ["customer", "email"]  # still split: the ladder is spent
    spec = next(e for e in events if e.kind == "chart_ready").spec
    assert chartable.unreadable(orchestrator.renderer.build(spec, result, "sakila")) is None


def test_the_shape_check_costs_nothing_when_no_chart_is_asked_for(store):
    """The rule is about charts, so a SQL-only Turn does not pay a model call for it, in either half."""
    for sql, columns in ((SPLIT, ["first_name", "last_name", "films"]), (LABELS_ONLY, ["name"])):
        seen, answering_model = answering(sql)
        with analysis_agent.override(model=answering_model):
            result = orchestrator.run_turn(store, store.create("alice", "sakila"), TOP_ACTORS, lambda e: None)
        assert len(seen) == 1 and result.table.columns == columns


LABELS_ONLY = "SELECT DISTINCT c.name FROM category c JOIN film_category fc ON fc.category_id = c.category_id"
MEASURED = "SELECT c.name, COUNT(*) AS films FROM category c JOIN film_category fc ON fc.category_id = c.category_id GROUP BY c.name"


def test_a_result_with_no_measure_is_refused_once_where_a_measure_can_be_added(store):
    """The other half of the chartable invariant, at the layer that can satisfy it: the Analysis Agent adds a COUNT(*)."""
    session = store.create("alice", "sakila")
    seen, answering_model = answering(LABELS_ONLY, MEASURED)
    events = []
    with analysis_agent.override(model=answering_model), visualization_agent.override(model=FunctionModel(lambda m, i: chart_spec(x="name", y="films", title="Sports leads"))):
        result = orchestrator.run_turn(store, session, "Which categories have films?", events.append, chart=True)
    assert len(seen) == 2 and seen[1] == ["retry-prompt"]
    assert [e.check for e in events if e.kind == "check_fired"] == ["no_measure"]
    assert result.table.columns == ["name", "films"] and "chart_ready" in kinds(events)


def test_case_1434_a_label_only_answer_the_model_keeps_ends_in_a_table_and_a_skipped_chart(store):
    """Held-out case 1434 died here: three refusals of a non-numeric y, then `Exceeded maximum output retries` and no answer.
    Now the analysis check fires once, the column check fires once, and the Turn ends with the table and the reason."""
    session = store.create("alice", "sakila")
    seen, answering_model = answering(LABELS_ONLY, LABELS_ONLY)
    prompts, drawing_model = drawing(chart_spec(x="name", y="name", title="Categories"), chart_spec(x="name", y="name", title="Categories"))
    events = []
    with analysis_agent.override(model=answering_model), visualization_agent.override(model=drawing_model):
        result = orchestrator.run_turn(store, session, "Which categories have films?", events.append, chart=True)
    assert isinstance(result, AnalysisResult) and result.table.columns == ["name"] and events[-1].kind == "turn_finished"
    assert [e.check for e in events if e.kind == "check_fired"] == ["no_measure", "columns_exist"]
    skipped = next(e for e in events if e.kind == "chart_skipped")
    assert skipped.code == "unbuildable" and "not numeric" in skipped.detail and len(prompts) == 2 and not events[-1].charted
    assert skipped.reason == chartable.PLAIN["unbuildable"]  # what the user reads is the sentence, not the renderer's text


def test_a_turn_that_trips_three_checks_still_finishes(store):
    """Every check climbs its ladder, so three checks the model never fixes cost five retries; the budget is sized to the ladder."""
    session = store.create("alice", "sakila")
    unordered_split_list = "SELECT first_name, last_name FROM actor LIMIT 5"
    seen, answering_model = answering(unordered_split_list)
    events = []
    with analysis_agent.override(model=answering_model), visualization_agent.override(model=FunctionModel(lambda m, i: chart_spec(x="first_name", y="last_name", title="t"))):
        result = orchestrator.run_turn(store, session, "Name five actors", events.append, chart=True)
    fired = [e.check for e in events if e.kind == "check_fired"]
    assert fired[:5] == ["split_identity", "split_identity", "no_measure", "unordered_limit", "unordered_limit"]  # every rung of every check
    assert len(seen) == 6 and isinstance(result, AnalysisResult) and len(result.table.rows) == 5


def test_the_output_retry_budget_is_the_rungs_bounded_by_the_request_limit():
    """Eleven checks hold sixteen rungs between them and a Turn holds eight requests, so the budget is the smaller of the two:
    a Turn cannot climb a rung it has no request for, and past the limit the sentence a user reads is about the steps and not
    about the retries. A final SQL that fails to run spends from the same budget."""
    from data_agents.agents import sql_checks
    from data_agents.agents.analysis import LADDER, LIMITS, agent
    assert sum(LADDER.values()) == 16 and LIMITS.request_limit == 8
    assert agent._max_output_retries == min(sum(LADDER.values()), LIMITS.request_limit - 1) == 7
    assert agent._max_tool_retries == 2
    assert set(LADDER) == {"zero_row", "split_identity", "no_measure", "unordered_limit"} | set(sql_checks.CHECKS)  # every check that refuses


def test_visualize_records_a_turn_that_keeps_its_chart_and_asks_the_model_nothing_new(store):
    """The chart of a stored result is a Turn like any other, so reopening the Session shows it; the history is left alone."""
    session = store.create("alice", "sakila")
    turn(store, session, "How are films distributed across ratings?", "sakila_rating_distribution")
    before, events = store.history(session.id), []
    with visualization_agent.override(model=FunctionModel(lambda m, i: chart_spec(y="film_count", title="t"))):
        orchestrator.visualize_turn(store, session, events.append, ChartHints(chart_type="bar"))
    kept = store.turns_of(session.id)
    assert store.get(session.id).turns == 2 and kept[0].chart is None and kept[1].chart is not None
    assert kept[1].question == "Chart the previous result, chart_type = 'bar'" and kept[1].chart.renderer == orchestrator.renderer.FINGERPRINT
    assert len(store.history(session.id)) == len(before) and events[-1].kind == "turn_finished"


def test_history_without_charts_is_backfilled_from_each_turns_own_question(store):
    """Turns recorded before Chart Specs were kept get one, drawn from the question and result the original call saw."""
    session = store.create("alice", "sakila")
    rated = AnalysisResult(intent="distribution", sql="SELECT rating, COUNT(*) AS films FROM film GROUP BY rating", narrative="n",
                           table=QueryResult(columns=["rating", "films"], rows=[["PG", 194], ["R", 195]]))
    store.record_turn(session, "How are films rated?", [], rated, TurnMetrics(latency_ms={}))
    store.record_turn(session, "How many films?", [], AnalysisResult(intent="comparison", sql="SELECT COUNT(*) AS n FROM film", narrative="n",
                                                                     table=QueryResult(columns=["n"], rows=[[1000]])), TurnMetrics(latency_ms={}))
    store.record_turn(session, "By whom?", [], Clarification(question="Directors or actors?"), TurnMetrics(latency_ms={}))
    prompts, events = [], []

    def spec(messages, info):
        prompts.append(messages[-1].parts[-1].content)
        return chart_spec(y="films", title="Films by rating")

    with visualization_agent.override(model=FunctionModel(spec)):
        assert orchestrator.backfill_charts(store, events.append) == 1
    kept = store.turns_of(session.id)
    assert kept[0].chart.spec.title == "Films by rating" and kept[1].chart is None and kept[2].chart is None
    assert len(prompts) == 1 and "Question: How are films rated?" in prompts[0]  # the agent sees what it saw then
    assert [e.kind for e in events if e.kind in ("chart_ready", "chart_skipped")] == ["chart_ready", "chart_skipped"]
    assert store.turns_without_charts() == [] or [t.n for _, t in store.turns_without_charts()] == [2]  # the single value stays uncharted


def test_supplied_csv_is_charted_with_no_analysis_call(store, tmp_path):
    """The Visualization Agent charts data the user brought, and the caption has no SQL to hash."""
    csv_file = tmp_path / "categories.csv"
    csv_file.write_text("category,revenue\nSports,5314.2\nSci-Fi,4756.9\nAnimation,4656.3\n")
    result, events = supplied.from_csv(csv_file), []
    with replaying(TAPES / "sakila_category_revenue.json"):  # only its visualization response is consumed
        drawn = orchestrator.chart_result(csv_file.name, result, events.append)
    assert drawn is not None and "sql_written" not in kinds(events) and events[-1].metrics.requests == 1
    spec = orchestrator.renderer.build(next(e for e in events if e.kind == "chart_ready").spec, result, csv_file.name)
    assert spec["title"]["subtitle"][-1] == "Source: categories.csv · Data Agents"


def test_chart_hints_reach_the_agent_and_the_rendered_chart(store):
    """Chart hints: the user's preferences are stated in the prompt, and what the agent returns is still validated."""
    session = store.create("alice", "sakila")
    prompts = []

    def echo_the_hinted_title(messages, info):
        prompts.append(messages[-1].parts[-1].content)
        return chart_spec(y="film_count", title=prompts[0].split("title = '")[1].split("'")[0])

    events = []
    with replaying(TAPES / "sakila_rating_distribution.json"), visualization_agent.override(model=FunctionModel(echo_the_hinted_title)):
        orchestrator.run_turn(store, session, "How are films distributed across ratings?", events.append, chart=True,
                              hints=ChartHints(chart_type="bar", title="Films by rating"))
    assert "User preferences: chart_type = 'bar', title = 'Films by rating'" in prompts[0]
    ready = next(e for e in events if e.kind == "chart_ready")
    assert ready.spec.title == "Films by rating" and orchestrator.renderer.build(ready.spec, events[-1].result, "sakila")["title"]["text"] == "Films by rating"


def test_the_question_reaches_the_visualization_agent(store):
    """The words that ask for a form or an order ("as a scatter", "in alphabetical order") live only in the question, and the
    Visualization Agent used to see the result, the intent and the narrative but never the question itself."""
    session = store.create("alice", "sakila")
    prompts = []

    def read_the_question(messages, info):
        prompts.append(messages[-1].parts[-1].content)
        return chart_spec(y="film_count", title="Films by rating")

    question = "How are films distributed across ratings?"
    with replaying(TAPES / "sakila_rating_distribution.json"), visualization_agent.override(model=FunctionModel(read_the_question)):
        orchestrator.run_turn(store, session, question, [].append, chart=True)
    assert prompts[0].startswith(f"Question: {question}\n")


def test_charting_without_a_question_says_nothing_about_one():
    """`visualize` and user-supplied data have no question to carry: the words were spoken in an earlier Turn, or never."""
    result = AnalysisResult(intent="comparison", sql="s", narrative="n", table=QueryResult(columns=["c", "n"], rows=[["a", 1]]))
    assert "Question:" not in describe(result)
    assert describe(result, "which genres?").startswith("Question: which genres?\n")


DUPLICATES = ("SELECT f.rating, c.name AS category, COUNT(*) AS films FROM film f JOIN film_category fc ON fc.film_id = f.film_id "
              "JOIN category c ON c.category_id = fc.category_id GROUP BY f.rating, c.name ORDER BY films DESC LIMIT 12")


def drawing(*specs: ModelResponse):
    """The Visualization Agent's answers in order, and what its prompt said each time."""
    prompts: list[str] = []

    def draw(messages, info):
        prompts.append(" ".join(str(p.content) for p in messages[-1].parts))
        return specs[min(len(prompts), len(specs)) - 1]

    return prompts, FunctionModel(draw, model_name="drawing")


def test_an_illegible_spec_comes_back_to_the_visualization_agent_as_a_retry(store):
    """One label per bar is a rule the renderer cannot correct, so the reason goes to the model that chose the columns."""
    session = store.create("alice", "sakila")
    prompts, drawing_model = drawing(chart_spec(x="rating", y="films", title="R films lead"),      # rating repeats: illegible
                                     chart_spec(x="rating", y="films", color="category", title="R films lead"))
    events = []
    with analysis_agent.override(model=FunctionModel(lambda m, i: finish(DUPLICATES, intent="comparison"))), \
         visualization_agent.override(model=drawing_model):
        orchestrator.run_turn(store, session, "Films per rating and category", events.append, chart=True)
    assert len(prompts) == 2 and "cannot be read" in prompts[1] and "repeats on the category axis" in prompts[1]
    ready = next(e for e in events if e.kind == "chart_ready")  # the second spec charted, no ChartSkipped
    assert ready.spec.color == "category" and "chart_skipped" not in kinds(events)
    assert events[-1].metrics.requests == 3  # one analysis call and two chart calls: the retry is the whole cost


def test_a_spec_that_stays_illegible_skips_the_chart_rather_than_erroring_the_turn(store):
    """The bound is one retry. A model that cannot fix it leaves a Turn with its answer and the reason, never an exception."""
    session = store.create("alice", "sakila")
    illegible = chart_spec(x="rating", y="films", title="R films lead")
    prompts, drawing_model = drawing(illegible, illegible)
    events = []
    with analysis_agent.override(model=FunctionModel(lambda m, i: finish(DUPLICATES, intent="comparison"))), \
         visualization_agent.override(model=drawing_model):
        result = orchestrator.run_turn(store, session, "Films per rating and category", events.append, chart=True)
    skipped = [e for e in events if e.kind == "chart_skipped"]
    assert len(prompts) == 2 and skipped and "repeats on the category axis" in skipped[0].detail and skipped[0].code == "unreadable"
    assert isinstance(result, AnalysisResult) and not events[-1].charted and events[-1].kind == "turn_finished"


def reshaping(charts: list[ModelResponse]):
    """Two FunctionModels: the Analysis Agent answers, the Visualization Agent replies with the given responses in order."""
    analyses, specs = [], []

    def analyze(messages, info):
        analyses.append(messages[-1])
        return finish("SELECT rating, COUNT(*) AS films FROM film GROUP BY rating" if len(analyses) == 1
                      else "SELECT rating, COUNT(*) AS films FROM film WHERE rating = 'R' GROUP BY rating")

    def draw(messages, info):
        specs.append(messages[-1])
        return charts[min(len(specs), len(charts)) - 1]

    return analyses, specs, analysis_agent.override(model=FunctionModel(analyze)), visualization_agent.override(model=FunctionModel(draw))


RESHAPE = ModelResponse(parts=[ToolCallPart("final_result_ReshapeRequest", {"instruction": "return one row per rating, R only"})])


def test_a_reshape_request_goes_back_to_the_analysis_agent_once(store):
    """The two-way connection between the agents: the instruction is a follow-up inside the same Turn, and its result is the
    one that stands."""
    session = store.create("alice", "sakila")
    analyses, specs, analysis, drawing = reshaping([RESHAPE, chart_spec(title="R-rated films")])
    events = []
    with analysis, drawing:
        result = orchestrator.run_turn(store, session, "How are films distributed across ratings?", events.append, chart=True)
    assert len(analyses) == 2 and len(specs) == 2 and kinds(events).count("reshape_requested") == 1
    assert isinstance(result, AnalysisResult) and len(result.table.rows) == 1 and "rating = 'R'" in result.sql
    assert store.last_result(session.id).sql == result.sql and store.get(session.id).turns == 1  # one Turn, reshaped
    assert events[-1].metrics.requests == 4  # two analysis calls and two chart calls, so the round-trip count stays honest


def test_a_second_reshape_request_ends_in_the_table_and_a_sentence(store):
    """Once is the bound; the second request used to raise and the user saw the exception. Now the Turn finishes with the
    reshaped table and one fixed sentence, and the technical instruction stays on the stream."""
    session = store.create("alice", "sakila")
    _, _, analysis, drawing = reshaping([RESHAPE, RESHAPE])
    events = []
    with analysis, drawing:
        result = orchestrator.run_turn(store, session, "How are films distributed across ratings?", events.append, chart=True)
    skipped = next(e for e in events if e.kind == "chart_skipped")
    assert isinstance(result, AnalysisResult) and events[-1].kind == "turn_finished" and not events[-1].charted
    assert skipped.code == "reshape" and skipped.reason == chartable.PLAIN["reshape"] and "R only" in skipped.detail
    assert store.get(session.id).turns == 1 and kinds(events).count("reshape_requested") == 1


def test_a_reshape_the_analysis_cannot_answer_ends_the_same_way(store):
    """The third member of the family the reshape bound covers, and the one that used to throw the whole Turn away: the chart side asked for
    a reshape and the Analysis Agent came back with a question or an abstention instead of a table. The answer the Turn already
    had is not the chart's to destroy, so the table stands and the chart does not."""
    session = store.create("alice", "sakila")
    analyses = []

    def analyze(messages, info):
        analyses.append(messages[-1])
        if len(analyses) == 1:
            return finish("SELECT rating, COUNT(*) AS films FROM film GROUP BY rating")
        return ModelResponse(parts=[ToolCallPart("final_result_Unanswerable", {"reason": "no such column"})])

    events = []
    with analysis_agent.override(model=FunctionModel(analyze)), visualization_agent.override(model=FunctionModel(lambda m, i: RESHAPE)):
        result = orchestrator.run_turn(store, session, "How are films distributed across ratings?", events.append, chart=True)
    skipped = next(e for e in events if e.kind == "chart_skipped")
    assert isinstance(result, AnalysisResult) and len(result.table.rows) == 5  # the answer it already had, not an error
    assert events[-1].kind == "turn_finished" and not events[-1].charted and skipped.code == "reshape"
    assert store.get(session.id).turns == 1


def test_a_reshape_with_no_analysis_behind_it_ends_the_same_way(store, tmp_path):
    """visualize --sql and --csv have no Analysis Agent behind them, so the request has nowhere to go: the table and the sentence."""
    csv_file = tmp_path / "ratings.csv"
    csv_file.write_text("rating,films\nR,195\nPG,194\n")
    events = []
    with visualization_agent.override(model=FunctionModel(lambda m, i: RESHAPE)):
        drawn = orchestrator.chart_result(csv_file.name, supplied.from_csv(csv_file), events.append)
    assert drawn is None and [e.kind for e in events][-2:] == ["chart_skipped", "turn_finished"] and events[-2].code == "reshape"


def test_a_check_the_model_insists_on_escalates_to_a_question_the_user_can_answer(store):
    """The ladder: the first refusal names the defect, the second says the answer may not stand and asks for the one
    question that settles it, and a Clarification ends the Turn. The user sees a question, never a doubtful table."""
    session = store.create("alice", "sakila")
    unordered = "SELECT title, length FROM film LIMIT 5"
    seen: list[str] = []

    def insist_then_ask(messages, info):
        seen.append(messages[-1].parts[-1].content if messages[-1].parts[-1].part_kind == "retry-prompt" else "")
        if "may not finish" in seen[-1]:
            return ModelResponse(parts=[ToolCallPart("final_result_Clarification", {"question": "Which five films: the longest, the newest, or any five?"})])
        return finish(unordered, intent="comparison")

    events = []
    with analysis_agent.override(model=FunctionModel(insist_then_ask, model_name="insisting")):
        result = orchestrator.run_turn(store, session, "Name five films and their lengths", events.append, chart=True)
    assert isinstance(result, Clarification) and "Which five" in result.question
    assert [e.check for e in events if e.kind == "check_fired"] == ["unordered_limit", "unordered_limit"]
    assert "LIMIT with no ORDER BY" in seen[1] and "may not finish" in seen[2] and "never for SQL" in seen[2]


def test_a_check_the_model_insists_on_three_times_stands_rather_than_erroring(store):
    """The ladder is bounded like everything else: after the escalation the answer offered again is the answer."""
    session = store.create("alice", "sakila")
    seen, answering_model = answering(*["SELECT title, length FROM film LIMIT 5"] * 3)
    with analysis_agent.override(model=answering_model):
        result = orchestrator.run_turn(store, session, "Name five films and their lengths", lambda e: None)
    assert isinstance(result, AnalysisResult) and len(seen) == 3 and len(result.table.rows) == 5


def test_a_looping_model_fails_in_a_sentence_the_user_can_act_on(store):
    """No exception text reaches a user: the request limit becomes a next move, and the exception stays in `detail`."""
    session = store.create("alice", "sakila")
    forever = FunctionModel(lambda messages, info: ModelResponse(parts=[ToolCallPart("run_sql", {"sql": "SELECT 1"})]), model_name="looping")
    events = []
    with analysis_agent.override(model=forever), pytest.raises(UsageLimitExceeded):
        orchestrator.run_turn(store, session, "anything", events.append)
    error = events[-1]
    assert error.kind == "turn_error" and error.message == "The question took too many steps to answer; try a narrower one."
    assert "request_limit" in error.detail and "request_limit" not in error.message


def test_a_turn_that_dies_inside_an_agent_run_still_says_what_it_spent(store):
    """Spend reached the stream on the success path only, so a Turn that raised inside a run reported nothing for the calls it
    had already paid for — and a request-limit loop is the most expensive Turn there is, eight calls recorded as free."""
    session = store.create("alice", "sakila")
    forever = FunctionModel(lambda messages, info: ModelResponse(parts=[ToolCallPart("run_sql", {"sql": "SELECT 1"})]), model_name="looping")
    events = []
    with analysis_agent.override(model=forever), pytest.raises(UsageLimitExceeded):
        orchestrator.run_turn(store, session, "anything", events.append)
    spent = [e for e in events if e.kind == "model_call_done"]
    assert spent and sum(e.requests for e in spent) == LIMITS.request_limit


def test_the_ablation_flag_withholds_the_question_and_nothing_else(monkeypatch):
    """Instruction-following and inference are two numbers from one flag rather than a forked prompt, so the only difference
    between the runs is whether the Visualization Agent was told which chart to draw — which every one of VisEval's 1,150
    questions does."""
    monkeypatch.setenv("VISUALIZATION_SEES_QUESTION", "0")
    assert orchestrator._asked("as a pie chart") is None
    monkeypatch.delenv("VISUALIZATION_SEES_QUESTION")
    assert orchestrator._asked("as a pie chart") == "as a pie chart"


LANGUAGES_WITH_FILMS = ("SELECT l.name AS language, COUNT(f.film_id) AS films FROM language l LEFT JOIN film f ON f.language_id = l.language_id "
                       "GROUP BY l.name")


def test_the_grain_probe_hands_back_both_readings_once_when_the_flip_changes_the_answer(store):
    """Only English has films in Sakila, so the outer join answers six rows and the inner join one: a decision the question has
    to settle. The model gets both numbers back once, in plain words; the same SQL offered again stands, with the choice on record."""
    session = store.create("alice", "sakila")
    texts: list[str] = []
    seen, answering_model = answering(LANGUAGES_WITH_FILMS, LANGUAGES_WITH_FILMS, texts=texts)
    events = []
    with analysis_agent.override(model=answering_model):
        result = orchestrator.run_turn(store, session, "How many films are there per language?", events.append)
    assert len(seen) == 2 and seen[1] == ["retry-prompt"]
    assert "keeps rows with no match, as zeros or blanks: 6 rows" in texts[1] and "dropping them instead returns 1 row" in texts[1]
    assert "Clarification" in texts[1]  # the way out when the question does not decide
    assert [(e.knob, e.agreed) for e in events if e.kind == "grain_probed"] == [("join", False), ("count", True), ("join", False), ("count", True)]
    assert [e.check for e in events if e.kind == "check_fired"] == ["grain"]
    assert isinstance(result, AnalysisResult) and len(result.table.rows) == 6  # the model kept the absences, and that is its call


def test_a_distinct_count_is_worded_from_the_side_the_sql_took():
    from data_agents.contracts import QueryResult
    from data_agents.agents.grain import reading
    counted = QueryResult(columns=["s", "n"], rows=[["Fall", 2]])
    every = QueryResult(columns=["s", "n"], rows=[["Fall", 5]])
    assert reading("count", "SELECT s, COUNT(DISTINCT c) FROM t GROUP BY s", counted, every).startswith("as written, the answer counts each value once: 1 row")
    assert reading("count", "SELECT s, COUNT(c) FROM t GROUP BY s", every, counted).startswith("as written, the answer counts every row: 1 row")


def test_the_grain_probe_says_nothing_when_the_flip_is_a_no_op(store):
    """Every film has one film_id, so COUNT(DISTINCT) and COUNT agree, and the check costs one SQL execution and no model call."""
    session = store.create("alice", "sakila")
    seen, answering_model = answering("SELECT rating, COUNT(DISTINCT film_id) AS films FROM film GROUP BY rating")
    events = []
    with analysis_agent.override(model=answering_model):
        result = orchestrator.run_turn(store, session, "Films per rating?", events.append)
    assert len(seen) == 1 and [e.kind for e in events if e.kind in ("grain_probed", "check_fired")] == ["grain_probed"]
    assert events[-1].metrics.requests == 1 and len(result.table.rows) == 5


def test_an_anti_join_is_an_outer_join_by_construction_and_does_not_fire(store):
    """The 42 films no store stocks: inner, the twin returns nothing, which is not a reading the question could mean."""
    session = store.create("alice", "sakila")
    seen, answering_model = answering("SELECT f.title, f.film_id FROM film f LEFT JOIN inventory i ON i.film_id = f.film_id WHERE i.inventory_id IS NULL")
    events = []
    with analysis_agent.override(model=answering_model):
        result = orchestrator.run_turn(store, session, "Which films are not in stock anywhere?", events.append)
    assert len(seen) == 1 and len(result.table.rows) == 42 and [e.agreed for e in events if e.kind == "grain_probed"] == [True]


@pytest.mark.parametrize("error,expected", [
    (UnexpectedModelBehavior("Exceeded maximum retries (7) for output validation"), "The model could not produce an answer it was allowed to give; try rephrasing the question."),
    (ModelHTTPError(status_code=500, model_name="gpt-4.1", body={"error": "upstream"}), "The model service did not answer; try again in a moment."),
    (RuntimeError("session abcd reached its 20-turn cap"), "session abcd reached its 20-turn cap"),  # ours, already a sentence
    (KeyError("no user carol"), "no user carol"),
])
def test_every_failure_reads_as_a_sentence_and_never_as_the_frameworks_own_text(error, expected):
    """The framework's exceptions subclass RuntimeError, so the check for our own sentences must not catch them."""
    assert orchestrator.plain(error) == expected


def test_a_model_whose_final_sql_never_runs_fails_in_a_sentence(store):
    """Retries exhausted is the framework's error; the user reads a next move and the admin's `detail` keeps the cause."""
    session = store.create("alice", "sakila")
    broken = FunctionModel(lambda messages, info: finish("SELECT nothing FROM nowhere"), model_name="broken")
    events = []
    with analysis_agent.override(model=broken), pytest.raises(UnexpectedModelBehavior):
        orchestrator.run_turn(store, session, "anything", events.append)
    error = events[-1]
    assert error.kind == "turn_error" and error.message == orchestrator.plain(UnexpectedModelBehavior("x")) and "retries" in error.detail


def test_an_escalation_keeps_the_whole_problem_even_when_it_contains_an_abbreviation():
    """The second refusal restates the problem and swaps the advice for the escalation; a problem with "e.g." in it used to be cut at the dot."""
    from types import SimpleNamespace
    from pydantic_ai import ModelRetry
    from data_agents.agents import analysis
    ctx = SimpleNamespace(deps=analysis.AnalysisDeps(user=None, db=None, schema_document="", emit=lambda e: None))
    problem = "the answer keeps rows with no match: 6 rows (e.g. [1, None]); dropping them returns 1 row"
    messages = []
    for _ in range(2):
        with pytest.raises(ModelRetry) as raised:
            analysis._refuse(ctx, "split_identity", problem, "Leave one of them.")
        messages.append(str(raised.value))
    assert messages[0] == f"{problem}. Leave one of them." and messages[1] == f"{problem}. {analysis.ESCALATE}"
    analysis._refuse(ctx, "split_identity", problem, "Leave one of them.")  # the third offer stands


# The six rewrite checks (tests/fixtures/sql-checks.md), each end to end: the model offers the natural SQL, the check's twin disagrees, the
# retry names both numbers, and the model fixes the SQL or keeps it, per the check's rungs. FunctionModels, no tape and no key.

def checked(store, database, question, *sqls, chart=False):
    session = store.create("alice", database)
    texts: list[str] = []
    seen, answering_model = answering(*sqls, texts=texts)
    events = []
    with analysis_agent.override(model=answering_model):
        result = orchestrator.run_turn(store, session, question, events.append, chart=chart)
    return result, seen, texts, [(e.check, e.agreed) for e in events if e.kind == "check_probed"], [e.check for e in events if e.kind == "check_fired"]


def test_a_bare_column_is_refused_and_the_grouped_rewrite_is_accepted(store):
    result, seen, texts, probed, fired = checked(store, "chinook", "How many customers per country, and which city are they in?",
                                                 "SELECT Country, City, COUNT(*) AS customers FROM customers GROUP BY Country ORDER BY customers DESC",
                                                 "SELECT Country, COUNT(*) AS customers FROM customers GROUP BY Country ORDER BY customers DESC")
    assert len(seen) == 2 and "neither aggregated nor in the GROUP BY" in texts[1] and "arbitrary row" in texts[1]
    assert probed == [("bare_column", False)] and fired == ["bare_column"]  # the fixed SQL carries no bare column, so no second probe
    assert isinstance(result, AnalysisResult) and result.table.columns == ["Country", "customers"] and len(result.table.rows) == 24


def test_a_fanned_out_sum_hands_back_both_readings_once_and_the_kept_answer_stands(store):
    """One rung: a sum once per joined row is a reading the question can mean, so the model may keep it and say so."""
    wrong = ("SELECT c.name AS category, SUM(p.amount) AS revenue FROM category c JOIN film_category fc ON fc.category_id = c.category_id "
             "JOIN film f ON f.film_id = fc.film_id JOIN inventory i ON i.film_id = f.film_id JOIN rental r ON r.inventory_id = i.inventory_id "
             "JOIN payment p ON p.customer_id = r.customer_id GROUP BY c.name ORDER BY revenue DESC")
    result, seen, texts, probed, fired = checked(store, "sakila", "Total revenue per film category?", wrong, wrong)
    assert len(seen) == 2 and "repeat rows of payment" in texts[1] and "32653 rows over 14027 distinct payment rows" in texts[1]
    assert "finish again with the same SQL" in texts[1]
    assert probed == [("fan_out", False), ("fan_out", False)] and fired == ["fan_out"]  # the second offer is probed, not refused
    assert isinstance(result, AnalysisResult) and result.table.rows[0][0] == "Sports"


def test_a_fanned_out_sum_fixed_by_the_right_key_is_accepted(store):
    wrong = ("SELECT c.name AS category, SUM(p.amount) AS revenue FROM category c JOIN film_category fc ON fc.category_id = c.category_id "
             "JOIN film f ON f.film_id = fc.film_id JOIN inventory i ON i.film_id = f.film_id JOIN rental r ON r.inventory_id = i.inventory_id "
             "JOIN payment p ON p.customer_id = r.customer_id GROUP BY c.name ORDER BY revenue DESC")
    result, seen, texts, probed, fired = checked(store, "sakila", "Total revenue per film category?", wrong, wrong.replace("p.customer_id = r.customer_id", "p.rental_id = r.rental_id"))
    assert len(seen) == 2 and probed == [("fan_out", False), ("fan_out", True)] and fired == ["fan_out"]
    assert isinstance(result, AnalysisResult) and result.table.rows[0] == ["Sports", 5314.21]


def test_an_integer_division_is_refused_once_and_the_float_is_accepted(store):
    result, seen, texts, probed, fired = checked(store, "sakila", "What percentage of films are rated R?",
                                                 "SELECT COUNT(*) * 100 / (SELECT COUNT(*) FROM film) AS pct_r FROM film WHERE rating = 'R'",
                                                 "SELECT 100.0 * COUNT(*) / (SELECT COUNT(*) FROM film) AS pct_r FROM film WHERE rating = 'R'")
    assert len(seen) == 2 and "drops the fraction" in texts[1] and "[19]" in texts[1] and "[19.5]" in texts[1]
    assert probed == [("integer_division", False)] and fired == ["integer_division"]
    assert isinstance(result, AnalysisResult) and result.table.rows == [[19.5]]


def test_a_day_bound_on_a_timestamp_climbs_two_rungs_then_stands(store):
    """Two rungs, a defect: the first names the fix, the second says the answer may not stand and asks for the user's decision; the
    same SQL offered a third time is the answer."""
    cut = "SELECT COUNT(*) AS rentals FROM rental WHERE rental_date BETWEEN '2005-05-24' AND '2005-05-31'"
    result, seen, texts, probed, fired = checked(store, "sakila", "How many rentals from 24 to 31 May 2005?", cut, cut, cut)
    assert len(seen) == 3 and "cut at its midnight" in texts[1] and "[993]" in texts[1] and "[1156]" in texts[1]
    assert "may not finish" in texts[2] and "cut at its midnight" in texts[2]
    assert fired == ["date_bound", "date_bound"] and probed == [("date_bound", False)] * 3
    assert isinstance(result, AnalysisResult) and result.table.rows == [[993]]


def test_a_filter_that_drops_empties_hands_back_both_readings_and_the_kept_answer_stands(store):
    kept = "SELECT Country, COUNT(*) AS customers FROM customers WHERE State != 'CA' GROUP BY Country ORDER BY customers DESC"
    result, seen, texts, probed, fired = checked(store, "chinook", "How many customers per country are outside California?", kept, kept)
    assert len(seen) == 2 and "drops the rows where it is empty (NULL)" in texts[1] and "as written 7 rows" in texts[1] and "keeping them returns 24 rows" in texts[1]
    assert fired == ["null_filter"] and isinstance(result, AnalysisResult) and len(result.table.rows) == 7


def test_a_text_sort_is_refused_and_the_cast_is_accepted(store):
    session = store.create("bob", "northwind_small")
    texts: list[str] = []
    seen, answering_model = answering("SELECT LastName, Extension FROM Employee ORDER BY Extension DESC LIMIT 3",
                                      "SELECT LastName, Extension FROM Employee ORDER BY CAST(Extension AS REAL) DESC LIMIT 3", texts=texts)
    events = []
    with analysis_agent.override(model=answering_model):
        result = orchestrator.run_turn(store, session, "Which three employees have the highest extensions?", events.append)
    assert len(seen) == 2 and "sorts text" in texts[1] and "['5467', '5176', '465']" in texts[1] and "['5467', '5176', '3457']" in texts[1]
    assert [e.check for e in events if e.kind == "check_fired"] == ["text_sort"]
    assert isinstance(result, AnalysisResult) and [r[0] for r in result.table.rows] == ["Davolio", "Peacock", "Fuller"]


def test_the_probes_cost_no_model_call_when_every_twin_agrees(store):
    """A statement carrying three classes at once, none of them live: three twins run, nothing fires, one request. The sort key
    is a number stored as a number, so the text sort has no class to probe."""
    session = store.create("alice", "sakila")
    sql = ("SELECT c.name AS category, SUM(p.amount) AS revenue, COUNT(*) AS payments FROM payment p JOIN rental r ON p.rental_id = r.rental_id "
           "JOIN inventory i ON r.inventory_id = i.inventory_id JOIN film f ON i.film_id = f.film_id JOIN film_category fc ON f.film_id = fc.film_id "
           "JOIN category c ON fc.category_id = c.category_id WHERE c.name != 'Sports' AND p.payment_date < '2006-01-01' GROUP BY c.category_id ORDER BY revenue DESC")
    seen, answering_model = answering(sql)
    events = []
    with analysis_agent.override(model=answering_model):
        result = orchestrator.run_turn(store, session, "Revenue per category other than Sports?", events.append)
    assert len(seen) == 1 and sorted(e.check for e in events if e.kind == "check_probed") == ["bare_column", "fan_out", "null_filter"]
    assert all(e.agreed for e in events if e.kind == "check_probed") and not [e for e in events if e.kind == "check_fired"]
    assert isinstance(result, AnalysisResult) and events[-1].metrics.requests == 1
