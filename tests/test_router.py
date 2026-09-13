"""The follow-up router: the phrasing table first, then the plumbing that executes the plan, then the classifier's
measured accuracy on the table, recorded once live and replayed.

Two outcomes only. `present_only` charts the stored result under the user's new words with no analysis call; `new_analysis`
is today's path. The table's third column is what a mis-route costs, which is never more than today's cost: a wasted analysis
call one way, a stale chart the panel names the other, or a reshape request that runs the analysis after all.
"""

import json

import pytest
from pydantic_ai.messages import ModelResponse, ToolCallPart
from pydantic_ai.models.function import FunctionModel

from data_agents import orchestrator
from data_agents.agents import router
from data_agents.contracts import AnalysisResult, Clarification
from data_agents.agents.analysis import agent as analysis_agent
from data_agents.agents.visualization import agent as visualization_agent
from data_agents.system.sessions import SessionStore
from tests.evals.runner import TAPES
from tests.evals.tape import recording, replaying

PREVIOUS = ("How much revenue does each film category bring in?", ["category", "revenue"])
# follow-up -> plan. Extended from the brief's appendix B before any code was written; a phrasing about the same table in a
# different look is present_only, anything that needs other rows or columns is new_analysis, and both together is new_analysis.
PHRASINGS = {
    "make that a bar chart instead": "present_only",
    "sort it alphabetically": "present_only",
    'can you title it "Revenue by category"': "present_only",
    "show it as a pie": "present_only",
    "stack the bars": "present_only",
    "as a line chart please": "present_only",
    "flip it so the categories are on the left": "present_only",
    "order by revenue, smallest first": "present_only",
    "same thing but as a scatter": "present_only",
    "show me that chart again": "present_only",
    "what was the total again?": "present_only",
    "put the revenue on the x axis": "present_only",
    "colour the bars by category": "present_only",
    "can I see it descending?": "present_only",
    "draw it": "present_only",
    "exclude Sports": "new_analysis",
    "only the top five": "new_analysis",
    "break that down by store": "new_analysis",
    "and per month?": "new_analysis",
    "make it a bar chart and drop the Sports row": "new_analysis",
    "is that including tax?": "new_analysis",
    "what's the weather tomorrow": "new_analysis",
    "same for 2006 only": "new_analysis",
    "how many rentals is that per category?": "new_analysis",
    "compare it with last year": "new_analysis",
    "show the average instead of the total": "new_analysis",
    "which category has the most films?": "new_analysis",
    "add a column with the number of rentals": "new_analysis",
    "just Comedy and Drama": "new_analysis",
    "as a percentage of the whole": "new_analysis",
}
ROUTER_TAPE = TAPES / "router_phrasings.json"


def plan(name: str) -> FunctionModel:
    return FunctionModel(lambda messages, info: ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, {"response": name})]), model_name=name)


def finish(sql: str) -> ModelResponse:
    return ModelResponse(parts=[ToolCallPart("final_result_finish_analysis", {"intent": "comparison", "sql": sql, "narrative": "n"})])


def chart(**fields) -> ModelResponse:
    spec = {"chart_type": "bar", "x": "rating", "y": "films", "title": "t"} | fields
    return ModelResponse(parts=[ToolCallPart("final_result_ChartSpec", spec)])


RESHAPE = ModelResponse(parts=[ToolCallPart("final_result_ReshapeRequest", {"instruction": "return one row per rating"})])
FIRST = "SELECT rating, COUNT(*) AS films FROM film GROUP BY rating"


@pytest.fixture
def store(tmp_path, monkeypatch):
    return SessionStore(tmp_path / "sessions.db")


def first_turn(store, session, prompts=None):
    prompts = prompts if prompts is not None else []
    with analysis_agent.override(model=FunctionModel(lambda m, i: finish(FIRST))), visualization_agent.override(model=FunctionModel(lambda m, i: chart())):
        orchestrator.run_turn(store, session, "How are films distributed across ratings?", lambda e: None, chart=True)


def test_present_only_charts_the_stored_result_under_the_new_words_with_no_analysis_call(store):
    session = store.create("alice", "sakila")
    first_turn(store, session)
    analyses, prompts, events = [], [], []

    def draw(messages, info):
        prompts.append(messages[-1].parts[-1].content)
        return chart(sort="category asc")

    with analysis_agent.override(model=FunctionModel(lambda m, i: analyses.append(m) or finish(FIRST))), \
         visualization_agent.override(model=FunctionModel(draw)), router.agent.override(model=plan("present_only")):
        result = orchestrator.run_turn(store, session, "sort it alphabetically", events.append)
    assert analyses == [] and isinstance(result, AnalysisResult) and result.sql == FIRST  # the stored result, unchanged
    assert "Question: sort it alphabetically" in prompts[0]  # the follow-up is the question the Visualization Agent reads
    kinds = [e.kind for e in events]
    assert kinds.count("plan_chosen") == 1 and events[kinds.index("plan_chosen")].plan == "present_only" and "chart_ready" in kinds
    assert store.get(session.id).turns == 2 and store.turns_of(session.id)[-1].question == "sort it alphabetically"
    assert events[-1].metrics.requests == 2  # one router call, one chart call: three calls to one


def test_new_analysis_is_the_path_of_today(store):
    session = store.create("alice", "sakila")
    first_turn(store, session)
    analyses, events = [], []
    second = "SELECT rating, COUNT(*) AS films FROM film WHERE rating != 'R' GROUP BY rating"
    with analysis_agent.override(model=FunctionModel(lambda m, i: analyses.append(m) or finish(second))), router.agent.override(model=plan("new_analysis")):
        result = orchestrator.run_turn(store, session, "exclude R", events.append)
    assert len(analyses) == 1 and result.sql == second and [e.plan for e in events if e.kind == "plan_chosen"] == ["new_analysis"]


def test_a_reshape_on_the_present_only_path_runs_the_analysis_after_all(store):
    session = store.create("alice", "sakila")
    first_turn(store, session)
    analyses, events = [], []
    second = "SELECT rating, COUNT(*) AS films FROM film WHERE rating != 'R' GROUP BY rating"
    specs = iter([RESHAPE, chart()])
    with analysis_agent.override(model=FunctionModel(lambda m, i: analyses.append(m) or finish(second))), \
         visualization_agent.override(model=FunctionModel(lambda m, i: next(specs))), router.agent.override(model=plan("present_only")):
        result = orchestrator.run_turn(store, session, "without R", events.append)
    plans = [(e.plan, e.reason) for e in events if e.kind == "plan_chosen"]
    assert plans == [("present_only", "router"), ("new_analysis", "reshape")] and len(analyses) == 1 and result.sql == second
    assert "chart_ready" in [e.kind for e in events] and store.get(session.id).turns == 2  # and the words got their chart


def test_an_answer_to_a_clarification_is_never_routed(store):
    session = store.create("alice", "sakila")
    asked = ModelResponse(parts=[ToolCallPart("final_result_Clarification", {"question": "Which period?"})])
    with analysis_agent.override(model=FunctionModel(lambda m, i: asked)):
        assert isinstance(orchestrator.run_turn(store, session, "Compare the periods", lambda e: None), Clarification)
    events = []
    with analysis_agent.override(model=FunctionModel(lambda m, i: finish(FIRST))), router.agent.override(model=plan("present_only")):
        result = orchestrator.run_turn(store, session, "2005", events.append)
    assert isinstance(result, AnalysisResult) and "plan_chosen" not in [e.kind for e in events]


def test_a_first_turn_is_never_routed(store):
    events = []
    with analysis_agent.override(model=FunctionModel(lambda m, i: finish(FIRST))), router.agent.override(model=plan("present_only")):
        orchestrator.run_turn(store, store.create("alice", "sakila"), "How are films rated?", events.append)
    assert "plan_chosen" not in [e.kind for e in events]


@pytest.mark.parametrize("tape", [{"analysis": []}, {"analysis": [], "router": []}], ids=["key absent", "key empty"])
def test_a_tape_that_names_no_router_call_replays_it_as_never_called(tape, tmp_path):
    """A tape taken before the router existed carries no `router` key; a re-record of a run that never called it carries an
    empty one. Both replay as an empty player, so the run replays unchanged and one that *did* call the router stops loudly
    instead of reaching the network. Written here rather than counted off disk, because a census of the stored tapes stops
    being true the moment `--live` re-records them, which is the documented way to take them again."""
    path = tmp_path / "tape.json"
    path.write_text(json.dumps(tape))
    with replaying(path), pytest.raises(RuntimeError, match="tape exhausted"):
        router.classify("only the top 3", *PREVIOUS)


def test_the_classifier_on_the_phrasing_table(live):
    """One live pass over the table, recorded as a tape (~30 mini calls, about $0.05); the replay is the measured number."""
    only_router = {"router": router.agent}
    verdicts = {}
    with (recording if live else replaying)(ROUTER_TAPE, only_router):
        for phrasing, expected in PHRASINGS.items():
            verdicts[phrasing] = router.classify(phrasing, *PREVIOUS).output
    wrong = {p: v for p, v in verdicts.items() if v != PHRASINGS[p]}
    accuracy = 1 - len(wrong) / len(PHRASINGS)
    assert accuracy >= 0.95, f"{accuracy:.0%} on the table; mis-routed: {wrong}"  # below this, keep the code path and drop the model


@pytest.mark.parametrize("sql,code", [("SELECT COUNT(*) AS films FROM film", "single_value"),
                                      ("SELECT rating, COUNT(*) AS films FROM film WHERE rating = 'nope' GROUP BY rating", "empty")])
def test_present_only_passes_the_same_gate_as_any_other_chart(store, sql, code):
    """The stored result is charted through the gate every chart passes: a one-row or empty answer is printed and named, and
    the Visualization Agent is never called on it. The 1×N case that shipped a measure on both axes came back through this path."""
    session = store.create("alice", "sakila")
    with analysis_agent.override(model=FunctionModel(lambda m, i: finish(sql))):
        orchestrator.run_turn(store, session, "How many films?", lambda e: None)
    drawings, events = [], []
    with visualization_agent.override(model=FunctionModel(lambda m, i: drawings.append(m) or chart())), router.agent.override(model=plan("present_only")):
        result = orchestrator.run_turn(store, session, "draw it", events.append)
    assert drawings == [] and isinstance(result, AnalysisResult) and result.sql == sql
    skipped = [e for e in events if e.kind == "chart_skipped"]
    assert len(skipped) == 1 and skipped[0].code == code and events[-1].kind == "turn_finished" and not events[-1].charted
    assert store.get(session.id).turns == 2 and store.turns_of(session.id)[-1].chart is None
