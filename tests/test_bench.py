"""Tier 1 for the benchmark harness: the seam, the core rows, the VisEval adapter and the simulated user. No network, no key."""

import collections
import json
import sqlite3
import sys
from types import SimpleNamespace
from unittest import mock

import pytest

from tests.bench import scoring, tables, viseval
from tests.bench.case import Case
from tests.bench.viseval import FORMS, golden_table

HAVE_DATASET = (viseval.CACHE / "visEval_dataset" / "visEval.json").exists()  # the split tests read the real file, not a fixture

SINGLE = {"x_name": "Rank", "y_name": "COUNT(Rank)", "classify": [],
          "x_data": [["AssocProf", "AsstProf", "Professor"]], "y_data": [[8, 15, 27]]}
# Multi-series ground truth is a full grid: every series carries a value for every x, zero where there is no row at all.
MULTI = {"x_name": "fault", "y_name": "COUNT(fault)", "classify": ["Electrical", "Mechanical"],
         "x_data": [["MW", "PW"]], "y_data": [[0, 3], [1, 2]]}


def test_a_single_series_case_becomes_one_row_per_point():
    golden = golden_table(SINGLE)
    assert golden.columns == ["Rank", "COUNT(Rank)"] and golden.rows == [["AssocProf", 8], ["AsstProf", 15], ["Professor", 27]]


SCATTER = {"x_name": "avg(price)", "y_name": "min(price)", "classify": ["modern", "rustic"],
           "x_data": [[112.5], [90.0]], "y_data": [[75], [0]]}


def test_a_grouped_scatter_carries_its_own_x_values_per_series():
    """One x list per series, unlike a grouped line: reading it as a shared axis scored ten cases zero and blamed the agent."""
    golden = golden_table(SCATTER)
    assert golden.rows == [[112.5, "modern", 75], [90.0, "rustic", 0]]  # and a zero here is a point, not padding


def test_a_multi_series_case_names_its_series_and_drops_the_padding():
    """A database cannot return a row for a combination that has none, so a padded zero is not a missing answer."""
    golden = golden_table(MULTI)
    assert golden.columns == ["fault", "series", "COUNT(fault)"]
    assert golden.rows == [["PW", "Electrical", 3], ["MW", "Mechanical", 1], ["PW", "Mechanical", 2]]


# The chart row is VisEval's own `chart_check`: the gold name must appear in ours, so a bar drawn for a
# Pie is the miss it is, and a grouped bar passes for a Bar and, unless the question said "stacked", for a Stacked Bar —
# which is their own leniency and not ours.

def test_a_gold_chart_name_means_the_mark_that_name_means_and_not_the_one_we_draw():
    assert FORMS["Pie"] == ("arc", False)  # the House Style draws a bar above three parts, and that is scored as the miss it is
    assert FORMS["Stacked Bar"] == ("bar", True) and FORMS["Bar"] == ("bar", False)


@pytest.mark.parametrize("gold,stacked_bar,ours,expected", [
    ("Pie", None, "bar", False),
    ("Pie", None, "pie", True),
    ("Bar", None, "grouping bar", True),          # their leniency: a series on a Bar case is still a bar
    ("Stacked Bar", False, "grouping bar", True),  # and a grouped bar passes for a Stacked Bar the question did not call stacked
    ("Stacked Bar", True, "grouping bar", False),  # but not when it did
    ("Stacked Bar", True, "stacked bar", True),
    ("Grouping Line", None, "line", False),
    ("Line", None, "grouping line", True),
])
def test_the_chart_row_is_visevals_name_match(gold, stacked_bar, ours, expected):
    case = _bench_case(gold, stacked_bar=stacked_bar)
    rows = viseval.judge(case.extra, {"chart": ours, "mark": "bar", "encoding": {}, "data": []})
    assert rows["chart"] is expected and rows["data"] is False and rows["order"] is None  # no data was handed over; no order asked
    assert ("chart" in rows["reasons"]) is not expected


def _bench_case(chart: str, vis_obj: dict = SINGLE, channel_specified=(), sort_by=None, stacked_bar=None, database="d",
                gold_sql="SELECT 1", gold_rows=None) -> Case:
    mark, stacked = FORMS[chart]
    extra = {"chart": chart, "mark": mark, "stacked": stacked, "golden": golden_table(vis_obj), "binning": "",
             "vis_obj": vis_obj | {"sort": vis_obj.get("sort")}, "channel_specified": list(channel_specified),
             "sort_by": sort_by, "stacked_bar": stacked_bar}
    return Case(id="x", dataset="viseval", database=database, question="q", gold_sql=gold_sql, gold_rows=gold_rows, hardness="Easy", extra=extra)


def _built(rows: list[list], columns=("a", "n"), sort=None, chart_type="bar"):
    """A chart the renderer actually built from a small result, because `chart_info` reads the drawn spec and not the ChartSpec."""
    from data_agents.charts import renderer
    from data_agents.contracts import AnalysisResult, ChartSpec, QueryResult
    result = AnalysisResult(intent="comparison", sql="", narrative="", table=QueryResult(columns=list(columns), rows=rows))
    spec = ChartSpec(chart_type=chart_type, x=columns[0], y=columns[-1], color=columns[1] if len(columns) == 3 else None, sort=sort, title="t")
    return result, spec, renderer.build(spec, result, "d")


def test_chart_info_keeps_the_specs_roles_on_a_bar_the_renderer_laid_horizontally():
    """Eight categories flip the bar horizontal, so the measure is drawn on x; VisEval's checks still see the category as x,
    because the orientation is the renderer's room decision and not a change to what the chart is about."""
    from tests.bench.viseval_info import chart_info
    result, spec, vega_lite = _built([[f"category {i}", 10 - i] for i in range(8)])
    assert vega_lite["encoding"]["x"]["field"] == "n"  # horizontal
    info = chart_info(vega_lite, result)
    assert info["chart"] == "bar" and info["encoding"]["x"]["type"] == "nominal" and info["encoding"]["y"]["type"] == "quantitative"
    assert info["data"][0] == {"field_x": "category 0", "field_y": 10.0}
    assert info["encoding"]["y"]["scale"]["range"] == [0, 1]  # the label axis runs down y, the measure along a line
    assert info["encoding"]["x"]["scale"]["domain"] == [f"category {i}" for i in range(8)]  # the house default: by measure, descending


def test_chart_info_names_a_series_the_way_their_deconstruction_does():
    from tests.bench.viseval_info import chart_info
    result, spec, vega_lite = _built([["a", "s1", 1], ["a", "s2", 2], ["b", "s1", 3], ["b", "s2", 4]], columns=("a", "s", "n"))
    info = chart_info(vega_lite, result)
    assert info["chart"] == "grouping bar" and set(info["encoding"]) == {"x", "y", "fill"} and info["data"][0]["field_fill"] == "s1"
    result, spec, vega_lite = _built([["2012", "s1", 1], ["2012", "s2", 2], ["2013", "s1", 3], ["2013", "s2", 4]], columns=("year", "s", "n"), chart_type="line")
    info = chart_info(vega_lite, result)
    assert info["chart"] == "grouping line" and "stroke" in info["encoding"] and info["encoding"]["x"]["type"] == "quantitative"  # a year is a number to them


def test_the_data_row_matches_a_month_bucket_to_visevals_own_spelling():
    """`BIN date BY MONTH` writes the bucket as 'Feb'; a GROUP BY returns '2018-02'. Their `compare_time_strings` is the
    published answer to what `_same_measures` used to do by hand, and it is stricter: the numbers must match too."""
    binned = {"x_name": "date_of_treatment", "y_name": "COUNT(date_of_treatment)", "classify": [], "x_data": [["Feb", "Mar"]], "y_data": [[2, 13]], "sort": None}
    case = _bench_case("Bar", binned, channel_specified=("x", "y"))
    result, spec, vega_lite = _built([["2018-02", 2], ["2018-03", 13]], columns=("month", "treatment_count"))
    assert viseval.score(case, result, spec, vega_lite)["data"] is True
    result, spec, vega_lite = _built([["2018-02", 3], ["2018-03", 10]], columns=("month", "treatment_count"))
    assert viseval.score(case, result, spec, vega_lite)["data"] is False


def test_their_data_check_drops_the_padded_empty_buckets_of_a_bar_on_both_sides():
    """VisEval pads a bar's empty buckets with a zero, which no GROUP BY returns; its own check filters zeros before comparing."""
    padded = {"x_name": "Start_from", "y_name": "n", "classify": [], "x_data": [["2003", "2004", "2005", "2008"]], "y_data": [[1, 0, 0, 1]], "sort": None}
    result, spec, vega_lite = _built([[2003, 1], [2008, 1]], columns=("year", "n"))
    assert viseval.score(_bench_case("Bar", padded), result, spec, vega_lite)["data"] is True


def test_the_order_row_is_read_off_the_drawn_scale():
    """The case asks for the measure descending on the y axis; a bar the House Style sorted by magnitude passes, a bar sorted
    alphabetically does not, and a case that asks for no order is not scored on one."""
    sorted_gold = SINGLE | {"sort": {"channel": "y", "order": "descending"}}
    rows = [["AssocProf", 8], ["AsstProf", 15], ["Professor", 27]]
    result, spec, vega_lite = _built(rows, columns=("Rank", "COUNT(Rank)"))
    assert viseval.score(_bench_case("Bar", sorted_gold, sort_by="axis"), result, spec, vega_lite)["order"] is True
    result, spec, vega_lite = _built(rows, columns=("Rank", "COUNT(Rank)"), sort="category asc")
    assert viseval.score(_bench_case("Bar", sorted_gold, sort_by="axis"), result, spec, vega_lite)["order"] is False
    assert viseval.score(_bench_case("Bar"), result, spec, vega_lite)["order"] is None


def test_a_case_that_ended_without_a_chart_fails_every_published_row_it_asks():
    """A Clarification or a skip has nothing their checks can read; the rows are misses, not exceptions, so the denominator holds."""
    from data_agents.contracts import Clarification
    sorted_gold = SINGLE | {"sort": {"channel": "y", "order": "descending"}}
    rows = viseval.score(_bench_case("Bar", sorted_gold), Clarification(question="?"), None, None)
    assert (rows["chart"], rows["data"], rows["order"]) == (False, False, False) and rows["facets"] == {"gold chart": "Bar"}


@pytest.mark.parametrize("values,typed,declared", [
    (("1", "2"), [1, 2], "INTEGER"),
    (("1.5", "2"), [1.5, 2], "REAL"),
    (("a", "2"), ["a", "2"], "TEXT"),
    (("", "3"), [None, 3], "INTEGER"),        # an empty CSV cell is a null, not a zero and not an empty string
    (("", ""), [None, None], "TEXT"),
])
def test_a_csv_column_is_typed_by_every_value_in_it(values, typed, declared):
    column = tables.column(values)
    assert column == typed and tables.declare(column) == declared


def test_the_declared_type_is_one_sqlite_accepts():
    connection = sqlite3.connect(":memory:")
    for declared in ("INTEGER", "REAL", "TEXT"):
        connection.execute(f'CREATE TABLE "t_{declared}" ("c" {declared})')


# The dev/test split. The membership is a committed file rather than a seed, so these assert the discipline itself: a later
# change to the quota or the sampler cannot move a case across the line without one of these failing.

def _ids(path=None) -> set[str]:
    return set((path or viseval.DEV).read_text().split())


def test_the_dev_membership_is_committed_and_the_size_it_claims():
    assert len(_ids()) == 250


@pytest.mark.skipif(not HAVE_DATASET, reason="VisEval dataset not downloaded")
def test_dev_contains_every_case_the_build_has_already_been_tuned_on():
    """The stratified 100 found and fixed the Altair shorthand bug, the heatmap restriction, the timestamp class and the
    categorical-axes guard. A held-out set that contained any of them would be measuring its own answers."""
    tuned = {c.id for c in viseval.cases("sample")}
    assert tuned <= _ids() and len(tuned) == 100


@pytest.mark.skipif(not HAVE_DATASET, reason="VisEval dataset not downloaded")
def test_dev_and_test_are_disjoint_and_together_are_everything():
    dev, held, whole = viseval.cases("dev"), viseval.cases("test"), viseval.cases("all")
    dev_ids, held_ids = {c.id for c in dev}, {c.id for c in held}
    assert not (dev_ids & held_ids)
    assert dev_ids | held_ids == {c.id for c in whole} and len(whole) == 1150


@pytest.mark.skipif(not HAVE_DATASET, reason="VisEval dataset not downloaded")
def test_the_held_out_set_can_still_score_every_chart_form():
    """Dev over-samples the rare forms, so the split has to leave enough of each behind to mean anything."""
    held = collections.Counter(c.extra["chart"] for c in viseval.cases("test"))
    assert set(held) == set(FORMS) and min(held.values()) >= 10, held


@pytest.mark.skipif(not HAVE_DATASET, reason="VisEval dataset not downloaded")
def test_a_held_out_draw_is_committed_by_id_and_leaves_the_rest_of_the_set_untouched():
    """The held-out 900 costs about $7.82 whole, for a number that is only worth having once. Drawing 250 of it keeps the
    other 650 for a second question, and committing the ids is what stops the draw from quietly moving later."""
    drawn = viseval.cases("test-250")
    ids, dev_ids = {c.id for c in drawn}, _ids()
    assert len(ids) == 250 and not (ids & dev_ids)
    assert ids == set((viseval.DRAWS / "test-250.txt").read_text().split())
    assert collections.Counter(c.extra["chart"] for c in drawn) == collections.Counter(viseval.DEV_QUOTA)  # dev's shape, so the two compare


@pytest.mark.skipif(not HAVE_DATASET, reason="VisEval dataset not downloaded")
def test_the_committed_dev_split_is_the_one_its_derivation_draws():
    """`dev.txt` is the authority, and this is its provenance: the seeded draw still produces exactly it."""
    assert viseval.draw_dev() == sorted(viseval.DEV.read_text().split())


def test_an_unknown_split_names_the_ones_the_dataset_has():
    with pytest.raises(ValueError, match="sample, dev, test"):
        viseval.cases("train")


# What the seven-hour hang cost, pinned so it cannot cost it twice.

def test_a_model_call_has_a_timeout_so_a_hung_read_is_one_failed_case():
    """A held-out run blocked on one socket read for seven hours with 151 completed cases in memory. A request that never
    returns is not a slow request, and without this nothing above the socket can tell the difference."""
    from data_agents.agents.model_settings import SETTINGS
    assert SETTINGS.get("timeout")


def _adapter(**score_rows):
    """A dataset as the runner sees one: three names and nothing else."""
    return SimpleNamespace(cases=lambda split: [], register=lambda cases: None,
                           score=lambda case, result, spec, vega_lite: {"facets": {"kind": "k"}} | score_rows)


def test_a_live_run_keeps_every_case_it_has_already_paid_for(tmp_path, monkeypatch):
    """`run` accumulated results in memory and wrote the file once, after the last case, so the hang threw away all 151."""
    from tests.bench import runner
    calls = []

    def die(store, session, question, emit, chart=False, hints=None):
        calls.append(question)
        raise (KeyboardInterrupt() if len(calls) > 1 else RuntimeError("boom"))  # the second one is the hang being killed

    monkeypatch.setattr(runner.orchestrator, "run_turn", die)
    out = tmp_path / "results.json"
    with pytest.raises(KeyboardInterrupt):
        runner.run(_adapter(), [_bench_case("Bar"), _bench_case("Line")], tmp_path, out)
    assert len(json.loads(out.read_text())) == 1  # the first case survived the second one killing the process


def test_the_file_a_run_leaves_behind_can_be_read_for_bugs(tmp_path, monkeypatch):
    """The pre-flight for a live shakedown: what lands on disk has to carry, per case, the verdicts, what it cost, and — where
    it failed — the exception, the stage and the traceback. And the table has to render over that mix without treating a
    traceback as a verdict."""
    from tests.bench import runner, user
    from tests.bench.table import table
    outcomes = iter(["answer", "boom", "ask"])
    scripted, _ = _fake_turns("answer", "ask")

    def maybe_die(store, session, question, emit, chart=False, hints=None):
        if next(outcomes, "answer") == "boom":
            raise RuntimeError("boom")
        return scripted(store, session, question, emit, chart, hints)

    monkeypatch.setattr(runner.orchestrator, "run_turn", maybe_die)
    monkeypatch.setattr(runner, "about", lambda database: ("A test database.", ["t"]))
    out = tmp_path / "results.json"
    cases = [_bench_case("Bar", gold_rows=_q([["x", 1]], ["a", "n"])) for _ in range(3)]
    for i, case in enumerate(cases):
        case.id = f"case{i}"
    with user.agent.override(model=_person("Count each once.")):
        rows = runner.run(_adapter(), cases, tmp_path, out)

    stored = json.loads(out.read_text())
    assert len(stored) == len(rows) == 3                                   # every case on disk, written as it finished
    assert all("cost_usd" in r and "requests" in r and "seconds" in r for r in stored)  # including the one that died
    failed = next(r for r in stored if "error" in r)
    assert failed["error"] == "RuntimeError: boom" and failed["error_stage"] == "harness" and "boom" in failed["traceback"]
    picked = scoring.metrics(stored)
    assert "correct" in picked and not {"error", "error_stage", "traceback", "reply"} & set(picked)  # a traceback is not a verdict
    assert "1 Turn error" in table(stored, "t")


# Scoring `correct` against the gold SQL VisEval ships, rather than against `vis_obj`'s pre-baked x_data/y_data, whose
# shape varies by chart type — which is what scored ten grouped scatters zero and blamed the agent.

@pytest.mark.skipif(not HAVE_DATASET, reason="VisEval dataset not downloaded")
def test_every_case_ships_gold_sql_and_most_of_them_run():
    cases = viseval.cases("all")
    assert sum(bool(c.gold_sql) for c in cases) == 1002  # the other 148 bin, so their gold is not a query on its own
    assert all(c.extra["binning"] for c in cases if not c.gold_sql)
    assert sum(c.gold_rows is not None for c in cases) == 984  # 18 ship a query the guard or the driver refuses


@pytest.mark.skipif(not HAVE_DATASET, reason="VisEval dataset not downloaded")
def test_the_gold_sql_is_executed_through_the_same_read_only_handle_the_agent_gets():
    case = next(c for c in viseval.cases("all") if c.id == "4")
    # The guard parses and re-prints the SQL, so the driver names the column `COUNT(*)`; the rules compare positions.
    assert case.gold_rows.columns == ["Rank", "COUNT(*)"] and sorted(case.gold_rows.rows) == [["AssocProf", 2], ["AsstProf", 18], ["Professor", 14]]


@pytest.mark.skipif(not HAVE_DATASET, reason="VisEval dataset not downloaded")
def test_a_binned_case_ships_no_query_that_runs_and_keeps_the_pre_baked_table():
    binned = next(c for c in viseval.cases("all") if c.extra["binning"])
    assert binned.gold_sql == "" and binned.gold_rows is None and binned.extra["golden"].rows


# The result rows are BIRD's own evaluator, twice: `ex` verbatim and `correct` after projecting onto the gold's columns.

def _q(rows, columns=None):
    from data_agents.contracts import QueryResult
    return QueryResult(columns=list(columns or [f"c{i}" for i in range(len(rows[0]))]), rows=rows)


GOLD = [["2012", 477.53], ["2013", 450.58]]

BIRD = [
    ("identical", [["2012", 477.53], ["2013", 450.58]], True, True),
    ("other row order", [["2013", 450.58], ["2012", 477.53]], True, True),
    ("duplicate rows collapse", [["2012", 477.53], ["2012", 477.53], ["2013", 450.58]], True, True),
    ("other column order", [[477.53, "2012"], [450.58, "2013"]], False, True),   # ex is positional; correct projects
    ("extra column", [["2012", 100, 477.53], ["2013", 90, 450.58]], False, True),
    ("year as integer", [[2012, 477.53], [2013, 450.58]], False, False),          # no coercion: 2012 != "2012"
    ("rounded", [["2012", 477.5], ["2013", 450.6]], False, False),                # no tolerance
    ("wrong number", [["2012", 477.53], ["2013", 451.58]], False, False),
    ("missing row", [["2012", 477.53]], False, False),
    ("extra row", [["2012", 477.53], ["2013", 450.58], ["2011", 469.58]], False, False),
    ("missing column", [[477.53], [450.58]], False, False),
]


@pytest.mark.parametrize("agent,ex_expected,correct_expected", [(a, e, c) for _, a, e, c in BIRD], ids=[n for n, *_ in BIRD])
def test_ex_is_birds_rule_and_correct_is_birds_rule_projected(agent, ex_expected, correct_expected):
    assert scoring.ex(_q(agent), _q(GOLD)) is ex_expected and scoring.projected(_q(agent), _q(GOLD)) is correct_expected


def test_the_greedy_matching_bug_cannot_exist_under_exact_equality():
    """The old tolerant rule matched gold rows to agent rows greedily, so two gold rows within tolerance of one agent row could
    be scored either way depending on order. Exact set equality has no matching step: the near-equal row is simply not there."""
    near = _q([["2012", 477.53], ["2012", 477.535]])
    assert scoring.projected(_q([["2012", 477.53], ["2012", 477.535]]), near) is True   # equal is equal, in any order
    assert scoring.projected(_q([["2012", 477.535], ["2012", 477.53]]), near) is True
    assert scoring.projected(_q([["2012", 477.53], ["2012", 477.54]]), near) is False   # and near is not


def test_a_case_with_no_executable_gold_is_scored_on_the_datasets_own_rows_alone():
    from data_agents.contracts import AnalysisResult
    result = AnalysisResult(intent="comparison", sql="", narrative="", table=_q(GOLD))
    assert scoring._correct(_bench_case("Bar"), result) == (None, None, "gold failed")           # a query that did not run
    assert scoring._correct(_bench_case("Bar", gold_sql=""), result) == (None, None, "no gold")  # no query at all
    assert scoring._correct(_bench_case("Bar", gold_rows=_q(GOLD)), result) == (True, True, "gold sql")


# The seam itself: one Case, three names per dataset, one loop; rows are read, never chosen.

def test_every_adapter_offers_the_three_names_and_nothing_else_is_required():
    from tests.bench import nvbench2
    for adapter in (viseval, nvbench2):
        assert all(callable(getattr(adapter, name)) for name in ("cases", "register", "score"))


def test_a_big_live_run_is_acknowledged_with_limit_rather_than_forbidden_by_it():
    """The size guard refuses an unqualified big run and names `--limit` as the way to ask for one, so `--limit` has to be able
    to say yes. It was applied *before* the test, which made it a 300-case ceiling no flag could clear and left the message
    promising a route that did not exist — found when a 750-case held-out split could not be run at all."""
    import contextlib
    import io

    from tests.bench import runner

    def run_main(argv, cases):
        with mock.patch.object(sys, "argv", argv), mock.patch.object(runner, "adapter_named", lambda name: SimpleNamespace(
                cases=lambda split: cases, register=lambda cs: None, score=lambda *a: {})), \
                mock.patch.object(runner, "run", lambda *a, **k: []), mock.patch.object(runner, "table", lambda *a: ""):
            with contextlib.redirect_stderr(io.StringIO()) as err:
                try:
                    runner.main()
                except SystemExit:
                    return err.getvalue()
        return ""

    many = [_bench_case("Bar") for _ in range(750)]
    for i, c in enumerate(many):
        c.id = f"c{i}"
    out = ["--out", "/dev/null"]
    refused = run_main(["runner", "--dataset", "x", "--split", "test-authors", "--final"] + out, many)
    assert "ask for it with --limit" in refused                                  # unqualified, still refused
    assert run_main(["runner", "--dataset", "x", "--split", "test-authors", "--final", "--limit", "750"] + out, many) == ""
    assert run_main(["runner", "--dataset", "x", "--split", "test-authors"] + out, many)                # --final still required


SHARED = ("runner", "scoring", "table", "user", "rescore", "paired", "reply", "checks", "tables")


def test_the_core_rows_do_not_read_a_datasets_extra():
    """`extra` is the dataset's own gold and only its `score` may read it; the shared code never names a key of it."""
    from pathlib import Path
    for module in SHARED:
        assert "extra" not in Path(f"tests/bench/{module}.py").read_text(), module


def test_no_shared_module_names_a_dataset():
    """Every tool that takes an adapter takes it by name and assumes none: a default would score one dataset's run under
    another's rules. Prose may name a dataset — `tables.py` explains in words why two of them share it — so the code is read
    without its comments and docstrings."""
    import ast
    from pathlib import Path
    for module in SHARED:
        tree = ast.parse(Path(f"tests/bench/{module}.py").read_text())
        for node in ast.walk(tree):
            if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and ast.get_docstring(node):
                node.body = node.body[1:]
        code = ast.unparse(tree).lower()
        assert "viseval" not in code and "nvbench" not in code, module


def test_the_metric_rows_are_read_off_the_rows_and_a_new_datasets_rows_appear_unasked():
    rows = [{"id": "a", "correct": True, "charted": True, "f1@1": 0.5, "facets": {"k": "v"}, "cost_usd": 0.1, "spec": None, "mark": "bar"},
            {"id": "b", "correct": None, "charted": False, "f1@1": 0.0, "facets": {"k": "w"}, "cost_usd": 0.1, "spec": None, "mark": None}]
    assert scoring.metrics(rows) == ["correct", "charted", "f1@1"]  # facets, costs, the mark and a key that is None everywhere are not verdicts


def test_the_table_groups_by_hardness_and_by_every_facet_and_prints_both_conditions():
    from tests.bench.table import pct, table
    rows = [{"id": "a", "hardness": "Easy", "correct": False, "charted": False, "asked": True, "answered": True, "scored_by": "gold sql", "mark": None,
             "facets": {"gold chart": "Bar"}, "after": {"correct": True, "charted": True, "mark": "bar", "facets": {"gold chart": "Bar"}, "hardness": "Easy"}},
            {"id": "b", "hardness": "Hard", "correct": True, "charted": True, "asked": False, "answered": None, "scored_by": "gold sql", "mark": "bar",
             "facets": {"gold chart": "Pie"}}]
    text = table(rows, "t")
    assert "| hardness | n | correct | charted | asked | answered |" in text and "| gold chart | n |" in text
    assert "**2 cases, first pass**: correct 1/2 (50%)" in text and "**2 cases, after the simulated user's reply**: correct 2/2 (100%)" in text
    assert pct([{"p": 1.0}, {"p": 0.0}, {"p": None}], "p") == "50.0"  # a numeric row prints its mean, over the cases it applies to


# The paired reduction, which is what decides whether a difference between two runs is a result or noise.

def _row(case_id, **scores):
    base = {"id": case_id, "correct": True, "form": True, "mapping": True, "ordered": True, "reads": True}
    return base | scores


def test_a_paired_comparison_counts_the_cases_that_disagree():
    from tests.bench.paired import compare
    before = [_row("a"), _row("b", correct=False), _row("c")]
    after = [_row("a"), _row("b"), _row("c", correct=False)]
    n, lost, gained = compare(before, after)["correct"]
    assert (n, lost, gained) == (3, 1, 1)  # both runs score 2/3, and two cases moved: the totals hide what the pairs show


def test_the_paired_metrics_are_whatever_both_runs_carry_as_booleans():
    """A scoring change pairs on the rows it kept, and a dataset with rows of its own pairs without touching paired.py."""
    from tests.bench.paired import metrics
    before = [{"id": "a", "correct": True, "form": True, "spec": {}, "scored_by": "gold sql"}]
    after = [{"id": "a", "correct": False, "data": None, "spec": {}, "scored_by": "gold sql"}]
    assert metrics(before, after) == ["correct"]  # `form` is only in one run, `data` only in the other, the rest are not booleans


def test_a_metric_that_does_not_apply_is_left_out_of_the_pairing():
    """`ordered` is None where the question asked for no order; pairing those in would dilute the discordance with ties."""
    from tests.bench.paired import compare
    before = [_row("a", ordered=None), _row("b", ordered=True)]
    after = [_row("a", ordered=None), _row("b", ordered=False)]
    assert compare(before, after)["ordered"] == (1, 1, 0)


# The correction-layer reducer, whose whole purpose is to say which checks earn their place.

CHECKED = [
    {"id": "1", "charted": True, "answer": {"columns": ["a", "n"], "rows": [["x", 1], ["y", 2], ["z", 3]]},
     "events": [{"kind": "sql_failed", "reason": "rejected: only SELECT is allowed"}, {"kind": "rows_fetched"},
                {"kind": "check_fired", "check": "zero_row", "detail": ""},
                {"kind": "model_call_done", "stage": "visualization", "requests": 2}, {"kind": "chart_ready"}]},
    {"id": "2", "charted": False, "answer": None, "events": [{"kind": "chart_skipped", "reason": "single-value result"}]},
    {"id": "3", "charted": False, "answer": {"columns": ["first", "last", "n"], "rows": [["a", "p", 1], ["b", "q", 2], ["c", "r", 3]]},
     "events": [{"kind": "check_fired", "check": "split_identity", "detail": "first, last"},
                {"kind": "chart_skipped", "reason": "'first' repeats on the category axis"}]},
]


def test_a_check_counts_as_self_corrected_only_when_the_answer_was_actually_fixed():
    """The split-identity retry fired and the answer that stood still has two columns naming every row, so it is not a fix.
    That distinction is the whole value of the table: a check that fires and is never satisfied is a prompt problem."""
    from data_agents.system.metrics import FIXED, firings
    fired = dict(firings(CHECKED[0]["events"]))
    assert set(fired) == {"sql_rejected", "zero_row", "visualization_retry"}
    assert FIXED["zero_row"](CHECKED[0]["events"], CHECKED[0]["answer"])
    assert not FIXED["split_identity"](CHECKED[2]["events"], CHECKED[2]["answer"])


def test_the_three_skip_reasons_are_three_different_checks():
    """One `chart_skipped` event carries a single-value print, an empty result and an unreadable chart, which mean different
    things: the first two are the system declining to draw, the third is a chart it could not make read."""
    from data_agents.system.metrics import firings
    assert firings(CHECKED[1]["events"])[0][0] == "single_value_skip"
    assert firings(CHECKED[2]["events"])[-1][0] == "unreadable_skip"


def test_the_two_output_validators_are_two_rows_and_not_one(monkeypatch):
    """They fired 4 times in 100 between them and fixed 2, and nothing said which did what: an `output_validator` has no emit
    channel, so both showed only as a second request in the visualization stage."""
    from dataclasses import dataclass
    from data_agents.agents import visualization
    from data_agents.contracts import AnalysisResult, ChartSpec, QueryResult

    @dataclass
    class Ctx:  # the validators read exactly two things off the run context
        deps: visualization.VisualizationDeps
        retry: int = 0

    fired: list = []
    result = AnalysisResult(intent="comparison", sql="", narrative="",
                            table=QueryResult(columns=["a", "n"], rows=[["x", 1], ["y", 2], ["z", 3]]))
    ctx = Ctx(visualization.VisualizationDeps(result=result, emit=fired.append))
    spec = ChartSpec(chart_type="bar", x="nope", y="n", title="t")
    with pytest.raises(Exception):
        visualization.columns_exist(ctx, spec)
    assert [e.check for e in fired] == ["columns_exist"]

    fired.clear()
    duplicated = AnalysisResult(intent="comparison", sql="", narrative="",
                                table=QueryResult(columns=["a", "n"], rows=[["x", 1], ["x", 2], ["x", 3]]))
    ctx = Ctx(visualization.VisualizationDeps(result=duplicated, emit=fired.append))
    with pytest.raises(Exception):
        visualization.chart_reads(ctx, ChartSpec(chart_type="bar", x="a", y="n", title="t"))
    assert [e.check for e in fired] == ["chart_reads"]


def test_a_named_validator_owns_the_retry_it_caused():
    """`visualization_retry` counted the same firing a second time, anonymously. It keeps its row as the residual: a second
    request neither validator explains is the output schema being rejected, which is a different thing worth seeing."""
    from data_agents.system.metrics import firings
    events = [{"kind": "check_fired", "check": "chart_reads", "detail": "duplicate labels"},
              {"kind": "model_call_done", "stage": "visualization", "requests": 2}, {"kind": "chart_ready"}]
    assert [check for check, _ in firings(events)] == ["chart_reads"]
    assert [check for check, _ in firings(events[1:])] == ["visualization_retry"]


def test_a_check_that_never_fires_is_still_a_row():
    from data_agents.system.metrics import report
    assert "| empty_skip | 0 |" in report(CHECKED)  # dead weight has to be visible to be deleted
    assert "| unordered_limit | 0 |" in report(CHECKED) and "| no_measure | 0 |" in report(CHECKED)  # the two the table could not report


def test_the_newest_checks_count_as_fixed_only_when_the_final_answer_no_longer_trips_them():
    from data_agents.system.metrics import FIXED
    finished = lambda sql: [{"kind": "turn_finished", "result": {"sql": sql, "table": {}}}]
    assert FIXED["unordered_limit"](finished("SELECT a FROM t ORDER BY a LIMIT 5"), {"columns": ["a"], "rows": [[1]]})
    assert not FIXED["unordered_limit"](finished("SELECT a FROM t LIMIT 5"), {"columns": ["a"], "rows": [[1]]})
    grain_fired = lambda sql: [{"kind": "check_fired", "check": "grain", "detail": "join"}] + finished(sql)
    assert FIXED["grain"](grain_fired("SELECT a, COUNT(b) FROM t JOIN u ON t.id = u.id GROUP BY a"), {"columns": ["a"], "rows": [[1]]})
    assert not FIXED["grain"](grain_fired("SELECT a, COUNT(b) FROM t LEFT JOIN u ON t.id = u.id GROUP BY a"), {"columns": ["a"], "rows": [[1]]})
    assert FIXED["no_measure"]([], {"columns": ["a", "n"], "rows": [["x", 1]]})
    assert not FIXED["no_measure"]([], {"columns": ["a"], "rows": [["x"]]})


def test_firings_are_crossed_with_correctness_where_the_run_knows_it():
    """The crossing that took a scratch script to learn: of the wrong answers, how many fired anything, and of the Turns that fired,
    how many were right anyway. Production rows carry no `correct`, and then the column and the sentence are absent."""
    from data_agents.system.metrics import crossed, report
    scored = [CHECKED[0] | {"correct": True}, CHECKED[1] | {"correct": False}, CHECKED[2] | {"correct": False},
              {"id": "4", "charted": True, "answer": None, "correct": False, "events": [{"kind": "rows_fetched"}]}]
    assert crossed(scored) == {"scored": 4, "wrong": 3, "wrong_fired": 2, "fired": 3, "fired_correct": 1}
    text = report(scored)
    assert "| on a wrong answer |" in text and "| zero_row | 1 | 0.25 | 1/1 (100%) | 0/1 | 0/1 |" in text
    assert "3 answers were wrong and **2** of them fired any check (67%); 3 Turns fired a check and 1 of those answers were right anyway" in text
    assert "on a wrong answer" not in report(CHECKED)


def test_the_grain_probe_is_reported_as_a_probe_and_crossed_with_correctness():
    """Its row is how often it ran, how often the twin disagreed, and how many of those Turns did not end in a correct answer."""
    from data_agents.system.metrics import probes, report
    turns = [{"id": "1", "correct": False, "charted": True, "events": [{"kind": "grain_probed", "knob": "join", "agreed": False}]},
             {"id": "2", "correct": True, "charted": True, "events": [{"kind": "grain_probed", "knob": "count", "agreed": True}]},
             {"id": "3", "correct": True, "charted": True, "events": [{"kind": "rows_fetched"}]}]
    assert probes(turns) == (2, 1, 1)
    assert "ran on 2 Turns (67%) and the twin disagreed on 1, 1 of which were not correct answers." in report(turns)


def test_the_reducer_reads_the_final_sql_off_the_last_turn():
    from data_agents.system.metrics import _final_sql
    events = [{"kind": "turn_finished", "result": {"question": "?"}}, {"kind": "turn_finished", "result": {"sql": "SELECT 2"}}]
    assert _final_sql(events) == "SELECT 2"


# The simulated user: a Turn that ends in a question cannot be scored by a harness
# that never answers, so every Clarification is answered by a mini model playing the person who asked, given nothing of the
# gold, as a second Turn; the first pass and the answer after the reply are two conditions, never blended.

def _asked_row(question="Which reading?"):
    return {"events": [{"kind": "check_fired", "check": "grain", "detail": "join"},
                       {"kind": "turn_finished", "result": {"question": question}, "metrics": {}}]}


def test_the_simulated_user_is_given_the_persons_side_and_nothing_of_the_gold():
    from tests.bench import user
    text = user.describe("How many dogs per owner?", "Count every row, or each dog once?", "A vet clinic.", ["dogs", "owners"])
    assert "How many dogs per owner?" in text and "Count every row, or each dog once?" in text and "A vet clinic." in text and "dogs, owners" in text
    assert "SELECT" not in text and "gold" not in text.lower() and "benchmark" not in text.lower() and "evaluat" not in user.INSTRUCTIONS.lower()
    with_history = user.describe("q", "c", "d", ["t"], narrative="The answer was 5.", columns=["a", "n"])
    assert "The answer was 5." in with_history and "a, n" in with_history


def test_the_only_channel_to_the_simulated_user_is_what_the_person_had(monkeypatch, tmp_path):
    """The claim is about the call and not the helper, so run the asking path and read the prompt the user's model was given:
    the case's gold SQL, its rows and its hardness have no way in, and the previous answer is the last *answered* Turn's and
    never the Clarification's own words."""
    from pydantic_ai.messages import ModelResponse, TextPart
    from pydantic_ai.models.function import FunctionModel

    from data_agents.contracts import AnalysisResult, QueryResult, TurnMetrics
    from data_agents.system.sessions import SessionStore
    from tests.bench import runner, user

    seen = []

    def record(messages, info):
        seen.append("\n".join(str(part.content) for m in messages for part in m.parts if hasattr(part, "content")))
        return ModelResponse(parts=[TextPart("Count each dog once.")])

    monkeypatch.setattr(runner, "about", lambda database: ("A veterinary clinic.", ["dogs", "owners"]))
    scripted, _ = _fake_turns("ask", "answer")
    monkeypatch.setattr(runner.orchestrator, "run_turn", scripted)
    case = _bench_case("Bar", gold_sql="SELECT COUNT(DISTINCT dog_id) FROM treatments", gold_rows=_q([["Poodle", 7]], ["breed", "n"]))
    store = SessionStore(tmp_path / "s.db")
    session = store.create("admin", "d")
    answered = AnalysisResult(intent="comparison", sql="SELECT 1", narrative="Eleven owners in all.",
                              table=QueryResult(columns=["owner", "dogs"], rows=[["Ann", 2]]))
    store.record_turn(session, "an earlier question", [], answered, TurnMetrics(latency_ms={}))
    with user.agent.override(model=FunctionModel(record, model_name="person")):
        runner.play(_adapter(), store, session, case)
    prompt = "\n".join(seen)
    assert prompt and "COUNT(DISTINCT dog_id)" not in prompt and "treatments" not in prompt and "Poodle" not in prompt
    assert "Easy" not in prompt and "Bar" not in prompt          # the hardness and the gold chart are benchmark fields
    assert "The analyst's previous answer said: Eleven owners in all." in prompt and "owner, dogs" in prompt  # the last answered Turn
    assert prompt.count("Keep rows with no match?") == 1         # the question back, and never also as the previous answer


def _person(words: str):
    """A FunctionModel playing the simulated user, so the runner's asking path runs without a key."""
    from pydantic_ai.messages import ModelResponse, TextPart
    from pydantic_ai.models.function import FunctionModel
    return FunctionModel(lambda messages, info: ModelResponse(parts=[TextPart(words)]), model_name="person")


def _fake_turns(*outcomes):
    """A `run_turn` that plays one scripted outcome per call, records it in the Session Store as the real one does, and keeps
    the questions it was asked."""
    from data_agents.contracts import AnalysisResult, CheckFired, Clarification, QueryResult, TurnFinished, TurnMetrics
    questions, script = [], list(outcomes)

    def run_turn(store, session, question, emit, chart=False, hints=None):
        questions.append(question)
        kind = script.pop(0)
        if kind == "ask":
            emit(CheckFired(check="grain", detail="join"))
            result = Clarification(question="Keep rows with no match?")
        elif kind == "ask other":
            result = Clarification(question="Which year?")
        else:
            result = AnalysisResult(intent="comparison", sql="SELECT 1", narrative="", table=QueryResult(columns=["a", "n"], rows=[["x", 1]]))
        metrics = TurnMetrics(latency_ms={}, requests=3, cost_usd=0.01)
        store.record_turn(session, question, [], result, metrics)
        emit(TurnFinished(result=result, metrics=metrics))
        return result
    return run_turn, questions


@pytest.fixture
def played(monkeypatch, tmp_path):
    from tests.bench import runner, user
    from data_agents.system.sessions import SessionStore
    monkeypatch.setattr(runner, "about", lambda database: ("A test database.", ["t", "u"]))

    def play(*outcomes, words="Keep the rows with no match.", case=None, row=None, run_turn=None, reply=True, adapter=None):
        scripted, questions = _fake_turns(*outcomes)
        monkeypatch.setattr(runner.orchestrator, "run_turn", run_turn or scripted)
        case = case or _bench_case("Bar", gold_rows=_q([["x", 1]], ["a", "n"]))
        store = SessionStore(tmp_path / "s.db")
        with user.agent.override(model=_person(words)):
            return runner.play(adapter or _adapter(), store, store.create("admin", "d"), case, row, reply=reply), questions
    return play


def test_a_clarification_is_answered_in_a_second_turn_and_both_conditions_are_scored(played):
    row, questions = played("ask", "answer")
    assert questions == ["q", "Keep the rows with no match."]
    assert row["asked"] and row["answered"] and row["reply"] == "Keep the rows with no match."
    assert row["correct"] is False and row["charted"] is False   # first pass: a question is not an answer
    assert row["after"]["correct"] is True                        # after the reply: the answer the user ends with
    assert row["requests"] == 7 and row["cost_usd"] == pytest.approx(0.02, abs=0.001)  # both Turns, plus the user's own call
    assert [e["kind"] for e in row["events"]].count("turn_finished") == 2  # one stream, so the reducer sees the firing and the answer


def test_every_clarification_is_answered_not_only_the_grain_probes(played):
    """The old simulated user answered the two grain flips from the gold SQL and left every other question as a wrong answer;
    this one plays the person, who can answer anything they are asked."""
    row, questions = played("ask other", "answer", words="The most recent year.")
    assert questions == ["q", "The most recent year."] and row["answered"] and row["after"]["correct"] is True


def test_a_run_with_replies_off_leaves_the_clarification_asked_and_unanswered_for_later(played):
    """The first pass alone, when the budget covers it and not the replies; `reply.py` plays them from the stored Sessions."""
    row, questions = played("ask", "answer", reply=False)
    assert questions == ["q"] and row["asked"] and row["answered"] is False and "after" not in row


def test_a_turn_that_did_not_ask_has_no_after_and_no_answered_row(played):
    row, questions = played("answer")
    assert questions == ["q"] and row["asked"] is False and row["answered"] is None and "after" not in row and row["correct"] is True


def test_a_turn_that_errored_is_neither_asked_nor_answered(played):
    def die(store, session, question, emit, chart=False, hints=None):
        raise RuntimeError("boom")

    row, _ = played(run_turn=die)
    assert row["error"] == "RuntimeError: boom" and row["asked"] is False and row["answered"] is None and row["correct"] is False


def test_a_failed_case_keeps_what_it_cost_and_enough_to_find_the_bug_with(played):
    """A failure has to be readable afterwards or the run that paid for it taught nothing: the exception's type, a traceback,
    the stage the orchestrator was in, and the money already spent on the model calls the Turn made before it died."""
    from data_agents.contracts import ModelCallDone, TurnError

    def die(store, session, question, emit, chart=False, hints=None):
        emit(ModelCallDone(stage="analysis", requests=3, input_tokens=10, cache_read_tokens=0, output_tokens=2, latency_ms=1.0, cost_usd=0.004))
        emit(TurnError(stage="analysis", message="a sentence for the user", detail="the technical text"))
        raise RuntimeError("boom")

    row, _ = played(run_turn=die)
    assert row["error_stage"] == "analysis" and "RuntimeError: boom" in row["traceback"] and "in die" in row["traceback"]
    assert row["requests"] == 3 and row["cost_usd"] == pytest.approx(0.004)  # the calls it made before it died are still spend
    assert row["seconds"] >= 0 and row["answer"] is None


def test_a_scorer_that_crashes_costs_one_case_and_not_the_run(played):
    """The failure the 88 unrun nvBench cases could produce: a dataset's `score` meeting a shape it has never met. It has to
    be one failed row with its traceback, because a stopped run throws away every case already paid for."""
    class Exploding:
        def register(self, cases):
            return None

        def score(self, case, result, spec, vega_lite):
            raise TypeError("gold has no mark")

    row, _ = played("answer", adapter=Exploding())
    assert row["error"] == "TypeError: gold has no mark" and row["error_stage"] == "harness"
    assert "gold has no mark" in row["traceback"] and row["id"] == "x" and row["database"] == "d"


def test_a_stored_first_turn_is_replied_to_without_replaying_the_question(played):
    """`reply.py`'s path: a run's asking Sessions are still in the Session Store, so the reply costs a Turn, not a rerun."""
    from tests.bench import runner
    stored = _asked_row() | {"cost_usd": 0.012, "requests": 3, "seconds": 1.0, "correct": False}
    # the stored Session already holds the Clarification, as the store does after a live first Turn
    from data_agents.contracts import Clarification, TurnMetrics
    real_play = runner.play

    def with_store(adapter, store, session, case, row, reply=True):
        store.record_turn(session, case.question, [], Clarification(question="Which reading?"), TurnMetrics(latency_ms={}))
        return real_play(adapter, store, session, case, row, reply)

    import tests.bench.runner as module
    module.play, restore = with_store, module.play
    try:
        row, questions = played("answer", row=stored)
    finally:
        module.play = restore
    assert questions == ["Keep the rows with no match."] and row["answered"] and row["after"]["correct"] is True and row["requests"] == 7


def test_a_rescored_case_scores_its_first_turn_and_its_reply_turn_apart():
    from tests.bench.rescore import rescore, turns
    finished_answer = {"kind": "turn_finished", "result": {"intent": "comparison", "sql": "SELECT 1", "narrative": "",
                                                            "table": {"columns": ["a", "n"], "rows": [["x", 1]]}}, "metrics": {}}
    events = _asked_row()["events"] + [finished_answer]
    assert [len(t) for t in turns(events)] == [2, 1]
    assert turns([{"kind": "turn_error"}]) == [[{"kind": "turn_error"}]]  # a Turn that errored is a Turn with no result
    case = _bench_case("Bar", gold_rows=_q([["x", 1]], ["a", "n"]))
    out = rescore([{"id": "x", "events": events, "cost_usd": 0.1}], _adapter(), {"x": case})[0]
    assert out["asked"] and out["answered"] and out["correct"] is False and out["after"]["correct"] is True and out["cost_usd"] == 0.1
    alone = rescore([{"id": "x", "events": _asked_row()["events"]}], _adapter(), {"x": case})[0]
    assert alone["asked"] and alone["answered"] is False and "after" not in alone


STORED = viseval.CACHE / "held-out-final.json"


@pytest.mark.skipif(not (HAVE_DATASET and STORED.exists()), reason="stored held-out run not present")
def test_held_out_run_2_re_scored_through_the_seam_reproduces_the_committed_rows():
    """The seam's own test: every committed number for held-out run #2 comes back from the adapter, `order` excepted,
    which moved with a later renderer change that the earlier table predates."""
    from tests.bench.rescore import rescore
    from tests.bench.table import pct
    rows = rescore(json.loads(STORED.read_text()), viseval, {c.id: c for c in viseval.cases("all")})
    assert {m: pct(rows, m) for m in ("ex", "correct", "chart", "data", "reads", "charted")} == {
        "ex": "129/209 (62%)", "correct": "171/209 (82%)", "chart": "229/250 (92%)", "data": "204/250 (82%)",
        "reads": "248/250 (99%)", "charted": "248/250 (99%)"}
    assert pct(rows, "order") == "98/115 (85%)"
