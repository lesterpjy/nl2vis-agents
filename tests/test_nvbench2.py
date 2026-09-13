"""Tier 1 for the nvBench 2.0 adapter: our answer in their vocabulary, their rule, and the committed split. No network, no key."""

import collections
import hashlib
import json

import pytest

from data_agents.contracts import AnalysisResult, ChartSpec, QueryResult
from tests.bench import nvbench2, viseval
from tests.bench import nvbench2_checks as checks
from tests.bench.case import Case

HAVE_DATASET = all((nvbench2.CACHE / "nvbench2" / f"{s}.json").exists() for s in nvbench2.THEIRS) and (viseval.CACHE / "visEval_dataset").exists()

GOLD = [{"mark": "line", "encoding": {"y": {"field": "authorder", "aggregate": "mean"}, "x": {"field": "paperid"}},
         "transform": [{"filter": {"field": "authorder", "gte": 2}}]},
        {"mark": "line", "encoding": {"y": {"field": "authorder", "aggregate": "mean"}, "x": {"field": "instid"}},
         "transform": [{"filter": {"field": "authorder", "gte": 2}}]}]
TYPES = {"authid": "quantitative", "instid": "quantitative", "paperid": "quantitative", "authorder": "quantitative"}


def _case(gold=GOLD, types=TYPES) -> Case:
    return Case(id="c", dataset="nvbench2", database="icfp_1__Authorship", question="q", extra={"csv_file": "icfp_1@Authorship.csv", "gold": gold, "types": types})


def _answer(sql: str, columns: list[str], chart_type="line", x=None, y=None, color=None, sort=None):
    result = AnalysisResult(intent="trend", sql=sql, narrative="", table=QueryResult(columns=columns, rows=[[1, 2.0]] * 2))
    spec = ChartSpec(chart_type=chart_type, x=x or columns[0], y=y or columns[-1], color=color, sort=sort, title="t")
    return result, spec


def test_our_answer_is_spelled_in_their_grammar():
    result, spec = _answer("SELECT paperid, AVG(authorder) AS avg_order FROM Authorship WHERE authorder >= 2 GROUP BY paperid", ["paperid", "avg_order"])
    assert nvbench2.predicted(result, spec) == {"mark": "line", "encoding": {"x": {"field": "paperid"}, "y": {"field": "authorder", "aggregate": "mean"}},
                                                "transform": [{"filter": {"field": "authorder", "gte": 2}}]}


@pytest.mark.parametrize("sql,columns,kind,x,y,color,sort,expected", [
    ("SELECT asset_id, COUNT(*) AS n FROM t GROUP BY asset_id", ["asset_id", "n"], "bar", None, None, None, None,
     {"mark": "bar", "encoding": {"x": {"field": "asset_id"}, "y": {"aggregate": "count"}}}),
    ("SELECT asset_id, COUNT(id) AS n FROM t WHERE asset_id IN (9, 15, 5) GROUP BY asset_id", ["asset_id", "n"], "bar", None, None, None, None,
     {"mark": "bar", "encoding": {"x": {"field": "asset_id"}, "y": {"aggregate": "count"}}, "transform": [{"filter": {"field": "asset_id", "oneOf": [9, 15, 5]}}]}),
    ("SELECT paperid, SUM(authid) AS total FROM t WHERE authid BETWEEN 60.85 AND 61.73 GROUP BY paperid", ["paperid", "total"], "bar", None, None, None, None,
     {"mark": "bar", "encoding": {"x": {"field": "paperid"}, "y": {"field": "authid", "aggregate": "sum"}}, "transform": [{"filter": {"field": "authid", "range": [60.85, 61.73]}}]}),
    ("SELECT paperid, SUM(authid) AS total FROM t WHERE authid >= 60 AND authid <= 61 GROUP BY paperid", ["paperid", "total"], "bar", None, None, None, None,
     {"mark": "bar", "encoding": {"x": {"field": "paperid"}, "y": {"field": "authid", "aggregate": "sum"}}, "transform": [{"filter": {"field": "authid", "range": [60, 61]}}]}),
    ("SELECT staff, COUNT(*) AS n FROM t GROUP BY staff", ["staff", "n"], "pie", None, None, None, "measure desc",
     {"mark": "arc", "encoding": {"theta": {"aggregate": "count"}, "color": {"field": "staff", "sort": "-theta"}}}),
    ("SELECT authid, instid, COUNT(*) AS n FROM t GROUP BY authid, instid", ["authid", "instid", "n"], "heatmap", "authid", "n", "instid", None,
     {"mark": "rect", "encoding": {"x": {"field": "authid"}, "y": {"field": "instid"}, "color": {"aggregate": "count"}}}),
    ("SELECT strftime('%Y', invoice_date) AS year, SUM(total) AS revenue FROM t GROUP BY year", ["year", "revenue"], "line", None, None, None, None,
     {"mark": "line", "encoding": {"x": {"field": "invoice_date", "timeUnit": "year"}, "y": {"field": "total", "aggregate": "sum"}}}),
    ("SELECT name = 'x' AS flag, 1 AS one FROM t WHERE 1 = 1", ["flag", "one"], "bar", None, None, None, None,
     {"mark": "bar", "encoding": {"x": {"field": "name = 'x'"}, "y": {"field": "1"}}}),  # no spelling in their grammar: keeps its own and cannot match
    # the literal written first: the filter is read from the column's side, so this is authid at least 60 and not at most
    ("SELECT paperid, SUM(authid) AS total FROM t WHERE 60 <= authid GROUP BY paperid", ["paperid", "total"], "bar", None, None, None, None,
     {"mark": "bar", "encoding": {"x": {"field": "paperid"}, "y": {"field": "authid", "aggregate": "sum"}}, "transform": [{"filter": {"field": "authid", "gte": 60}}]}),
    # a predicate their grammar cannot spell says nothing, rather than a field with no operator beside it
    ("SELECT paperid, COUNT(*) AS n FROM t WHERE authid NOT IN (1, 2) GROUP BY paperid", ["paperid", "n"], "bar", None, None, None, None,
     {"mark": "bar", "encoding": {"x": {"field": "paperid"}, "y": {"aggregate": "count"}}}),
    ("SELECT paperid, COUNT(*) AS n FROM t WHERE authid = instid GROUP BY paperid", ["paperid", "n"], "bar", None, None, None, None,
     {"mark": "bar", "encoding": {"x": {"field": "paperid"}, "y": {"aggregate": "count"}}}),
])
def test_each_construct_translates_to_their_spelling(sql, columns, kind, x, y, color, sort, expected):
    result, spec = _answer(sql, columns, kind, x, y, color, sort)
    assert nvbench2.predicted(result, spec) == expected


def test_their_match_is_equality_ignoring_key_order_and_list_order_but_not_key_count():
    a = {"mark": "bar", "encoding": {"x": {"field": "a"}, "y": {"aggregate": "count"}}}
    assert checks.matches(a, {"encoding": {"y": {"aggregate": "count"}, "x": {"field": "a"}}, "mark": "bar"})
    assert not checks.matches(a, a | {"transform": []})                                    # one key more is a different chart
    assert not checks.matches(a, {"mark": "bar", "encoding": {"x": {"field": "a", "sort": "y"}, "y": {"aggregate": "count"}}})
    t = {"transform": [{"filter": {"field": "a", "gte": 1}}, {"filter": {"field": "b", "lte": 2}}]}
    assert checks.matches(t, {"transform": t["transform"][::-1]})                           # a list matches in any order


def test_their_axis_swap_makes_the_two_orientations_one_chart():
    """A bar with a quantitative x against a nominal y is turned upright before comparing; two quantitative axes are ordered by name."""
    types = {"n": "quantitative", "kind": "nominal", "a": "quantitative", "b": "quantitative"}
    sideways = {"mark": "bar", "encoding": {"x": {"field": "n", "aggregate": "sum"}, "y": {"field": "kind"}}}
    assert checks.reverse_axes(dict(sideways), types)["encoding"] == {"x": {"field": "kind"}, "y": {"field": "n", "aggregate": "sum"}}
    assert checks.reverse_axes({"mark": "point", "encoding": {"x": {"field": "b"}, "y": {"field": "a"}}}, types)["encoding"] == {"x": {"field": "a"}, "y": {"field": "b"}}
    assert checks.reverse_axes({"mark": "point", "encoding": {"x": {"field": "a"}, "y": {"field": "b"}}}, types)["encoding"] == {"x": {"field": "a"}, "y": {"field": "b"}}


def test_scoring_leaves_the_prediction_and_the_gold_as_they_were():
    """Their swap rewrites the chart it is handed; a stored `predicted` must still read as the answer was made."""
    types = {"a": "quantitative", "b": "quantitative"}
    chart = {"mark": "point", "encoding": {"x": {"field": "b"}, "y": {"field": "a"}}}
    gold = [{"mark": "point", "encoding": {"x": {"field": "a"}, "y": {"field": "b"}}}]
    assert checks.evaluate([chart], gold, types, k=1)["hit"] == 1.0
    assert chart["encoding"]["x"] == {"field": "b"} and gold[0]["encoding"]["x"] == {"field": "a"}


def test_their_metrics_at_k_match_predictions_one_to_one_greedily():
    one = GOLD[0]
    assert checks.evaluate([one], GOLD, TYPES, k=1) == {"hit": 1.0, "recall": 0.5, "precision": 1.0, "f1": pytest.approx(2 / 3)}
    assert checks.evaluate([one, one], GOLD, TYPES, k=5) == {"hit": 1.0, "recall": 0.5, "precision": 0.5, "f1": 0.5}  # a gold matches once
    assert checks.evaluate([], GOLD, TYPES, k=1) == {"hit": 0.0, "recall": 0.0, "precision": 0.0, "f1": 0.0}
    assert checks.evaluate([one, GOLD[1]], GOLD, TYPES, k=1)["recall"] == 0.5  # K cuts the predictions, not the gold


def test_the_score_rows_are_the_papers_names_at_k_1_and_the_facets_are_the_gold_charts_and_the_level():
    result, spec = _answer("SELECT paperid, AVG(authorder) AS avg_order FROM Authorship WHERE authorder >= 2 GROUP BY paperid", ["paperid", "avg_order"])
    rows = nvbench2.score(_case(), result, spec, {"any": "chart"})
    assert (rows["hit@1"], rows["precision@1"], rows["recall@1"]) == (True, 1.0, 0.5) and rows["f1@1"] == pytest.approx(2 / 3)
    assert rows["facets"] == {"gold charts": "line", "ambiguity level": "2"}
    nothing = nvbench2.score(_case(), None, None, None)
    assert (nothing["hit@1"], nothing["precision@1"], nothing["f1@1"], nothing["predicted"]) == (False, 0.0, 0.0, None)


def test_an_unknown_split_names_the_ones_the_dataset_has():
    with pytest.raises(ValueError, match="dev, test, test-authors, all"):
        nvbench2.cases("train")


def test_the_dev_membership_is_committed_and_the_size_it_claims():
    assert len(set(nvbench2.DEV.read_text().split())) == 100


@pytest.mark.skipif(not HAVE_DATASET, reason="nvBench 2.0 or VisEval dataset not downloaded")
def test_the_committed_dev_split_is_the_one_its_derivation_draws_and_the_rest_is_held_out():
    assert nvbench2.draw_dev() == sorted(nvbench2.DEV.read_text().split())
    dev, held, whole = nvbench2.cases("dev"), nvbench2.cases("test"), nvbench2.cases("all")
    assert not ({c.id for c in dev} & {c.id for c in held}) and len(dev) + len(held) == len(whole) == 1498
    assert collections.Counter(c.extra["gold"][0]["mark"] for c in dev) == collections.Counter(nvbench2.DEV_QUOTA)


@pytest.mark.skipif(not HAVE_DATASET, reason="nvBench 2.0 or VisEval dataset not downloaded")
def test_carrying_the_authors_split_moved_no_case_id():
    """A case id hashes the table, the question and the gold — what a case *is* — and never which of their files shipped it, so
    making their splits addressable cannot move a committed membership. Both halves: the id is that hash, and
    `nvbench2-dev.txt` still selects exactly the 100 it was drawn as."""
    for case in nvbench2.cases("all"):
        payload = [case.extra["csv_file"], case.question, case.extra["gold"]]
        assert case.id == hashlib.sha1(json.dumps(payload, sort_keys=True).encode()).hexdigest()[:10], case.id
    assert {c.id for c in nvbench2.cases("dev")} == set(nvbench2.DEV.read_text().split())


@pytest.mark.skipif(not HAVE_DATASET, reason="nvBench 2.0 or VisEval dataset not downloaded")
def test_the_authors_test_split_is_addressable_and_is_held_out_by_their_line_as_well_as_ours():
    """The fair number to quote. Their two files share no case and our committed dev 100 lies entirely inside their dev, so
    their `test.json` is untouched under both protocols. `test` keeps what it has always meant — everything outside our dev —
    which is also why it is not the one to quote: it blends their test with the remainder of their dev."""
    theirs, dev, blended = nvbench2.cases("test-authors"), nvbench2.cases("dev"), nvbench2.cases("test")
    assert len(theirs) == 750 and all(c.extra["their_split"] == "test" for c in theirs)
    assert all(c.extra["their_split"] == "dev" for c in dev)
    assert not ({c.id for c in theirs} & {c.id for c in dev})
    assert {c.id for c in theirs} < {c.id for c in blended} and len(blended) == 1398


@pytest.mark.skipif(not HAVE_DATASET, reason="nvBench 2.0 or VisEval dataset not downloaded")
def test_the_same_question_shipped_twice_is_one_case():
    """Three rows across their two files repeat another row's table, question and gold, differing only in `steps`, the authors'
    own reasoning trace, which nothing here reads. Kept twice, a run pays for them twice and the table counts them twice while
    `paired` and `rescore` key by id and count them once — a metric that disagrees with its own pairing."""
    every = nvbench2.cases("all")
    assert len({c.id for c in every}) == len(every) == 1498


@pytest.mark.skipif(not HAVE_DATASET, reason="nvBench 2.0 or VisEval dataset not downloaded")
def test_a_split_holding_no_dev_case_is_named_so_the_runners_final_guard_fires():
    """`runner.main` refuses a held-out split without `--final` by reading the *name*, so a split that is held out but named
    otherwise would run unguarded. Decided here by what each split actually holds rather than by a list of names, so a split
    added later is covered by what it contains."""
    for adapter in (viseval, nvbench2):
        dev = {c.id for c in adapter.cases("dev")}
        for split in (s.replace("<N>", "250") for s in adapter.SPLITS.split(", ")):
            if not ({c.id for c in adapter.cases(split)} & dev):
                assert split.startswith("test"), f"{adapter.__name__}: {split} is held out and would run without --final"


@pytest.mark.skipif(not HAVE_DATASET, reason="nvBench 2.0 or VisEval dataset not downloaded")
def test_every_cases_gold_names_only_fields_of_its_own_table_and_is_genuinely_multi_valued():
    """The facts the release itself does not state, pinned: every table lives in VisEval's CSVs, every gold field is one of its columns
    (in lower case, which is how their gold spells them), and every case holds two to five gold charts. Twenty-five of them
    repeat a chart inside the set (14 at level 2, 11 at level 4), which their rule counts as two golds; kept as they ship it."""
    repeated = 0
    for case in nvbench2.cases("all"):
        fields = {spec["field"] for chart in case.extra["gold"] for spec in chart["encoding"].values() if "field" in spec}
        fields |= {t["filter"]["field"] for chart in case.extra["gold"] for t in chart.get("transform", [])}
        assert fields <= set(case.extra["types"]), case.id
        assert 2 <= len(case.extra["gold"]) <= 5, case.id
        repeated += len({json.dumps(g, sort_keys=True) for g in case.extra["gold"]}) != len(case.extra["gold"])
    assert repeated == 25


@pytest.mark.skipif(not HAVE_DATASET, reason="nvBench 2.0 or VisEval dataset not downloaded")
def test_register_builds_one_table_per_case_with_a_schema_document(tmp_path):
    from data_agents.data import registry
    from data_agents.data.database import ReadOnlyDatabase
    case = next(c for c in nvbench2.cases("dev") if c.extra["csv_file"] == "icfp_1@Authorship.csv") if any(
        c.extra["csv_file"] == "icfp_1@Authorship.csv" for c in nvbench2.cases("dev")) else nvbench2.cases("dev")[0]
    where = nvbench2.register([case], tmp_path)
    entry = registry.load(where)[case.database]
    assert entry.description == nvbench2.DESCRIPTION and (where / "schemas" / f"{case.database}.md").exists()
    db = ReadOnlyDatabase(nvbench2.build_database(case.extra["csv_file"], tmp_path))
    try:
        table = case.extra["csv_file"].rsplit(".", 1)[0].split("@")[1]
        assert {c.lower() for c in db.execute(f'SELECT * FROM "{table}" LIMIT 1').columns} == set(case.extra["types"])
    finally:
        db.close()
