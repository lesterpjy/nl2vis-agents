"""VisEval as the ground truth for charts: an open benchmark whose cases carry the numbers and the chart the answer should have.

Our own twelve cases and the edge sweep were written by us, so they cannot say whether the system is good, only whether it changed.
VisEval (IEEE VIS 2024) is 1,150 natural-language questions over 146 databases none of these agents has seen, each with the chart
type, the x and y the chart should carry, and the values behind them. The subset is stratified and seeded so every configuration
scores the same questions, which is what makes a paired comparison possible.

The adapter's three names: `cases(split)`, `register(cases)`, `score(case, result, spec, vega_lite)`. Its
published rows are VisEval's own `chart`, `data` and `order`, run as their code (`viseval_checks.py`, MIT) over the
`chart_info` we build from our Vega-Lite (`viseval_info.py`). `dry` is the free sweep, which only this dataset can offer.
"""

import functools
import json
import os
import random
import urllib.request
import zipfile
from pathlib import Path

from data_agents.charts import chartable, renderer
from data_agents.charts.form import field_type
from data_agents.contracts import AnalysisResult, ChartSpec, QueryResult
from data_agents.data.database import QueryFailed, ReadOnlyDatabase
from data_agents.data.sql_guard import SqlRejected
from tests.bench import tables
from tests.bench import viseval_checks as published
from tests.bench.case import Case
from tests.bench.scoring import core
from tests.bench.viseval_info import chart_info

CACHE = Path(os.environ.get("BENCH_DIR", ".bench"))
URL = "https://raw.githubusercontent.com/microsoft/VisEval/main/viseval_dataset.zip"
# Each of VisEval's chart names translated into the mark that name means, and whether it stacks. No judgement about what the
# House Style would rather draw lives here: `Pie` is `arc`, which we draw only at two or three parts, so a pie above that is
# the chart miss it is and the write-up decomposes the misses instead of hiding them in the scorer.
FORMS = {"Bar": ("bar", False), "Stacked Bar": ("bar", True), "Pie": ("arc", False), "Line": ("line", False),
         "Grouping Line": ("line", False), "Scatter": ("point", False), "Grouping Scatter": ("point", False)}
# Deliberately not VisEval's own mix (717 of its 1,150 cases are bars): the rare forms are the ones this build just widened
# into, so they are over-sampled. A number from this subset is ours to compare against ourselves, never against a leaderboard.
QUOTA = {"Bar": 30, "Pie": 15, "Line": 12, "Stacked Bar": 12, "Scatter": 12, "Grouping Scatter": 10, "Grouping Line": 9}
# The dev slice, 250 cases, sized by McNemar: the detectable difference is about sqrt(7.84 * discordance / n) at 5% and 80%
# power. The 3% measured between two runs of the *same* configuration is the noise floor, not the rate to size against —
# comparing two *different* configurations adds real differences to the discordant pairs, so the rate to size against is
# nearer 10 to 15%. At a realistic 10%, 250 resolves about 5.6 points and 100 about 8.9, so 250 is right-sized.
# Every rare form keeps roughly half its cases for the held-out set, so `test` can still score all seven.
DEV_QUOTA = {"Bar": 121, "Pie": 40, "Line": 26, "Stacked Bar": 22, "Scatter": 16, "Grouping Scatter": 15, "Grouping Line": 10}
SEED = 11
DEV = Path(__file__).parent / "dev.txt"  # the membership itself, not the seed: re-sampling must not be able to move a case
DRAWS = Path(__file__).parent  # and the same for each held-out draw: `test-250.txt` beside `dev.txt`
DESCRIPTION = "A VisEval benchmark database."
SPLITS = "sample, dev, test, test-<N>, all"


def cases(split: str, cache: Path = CACHE) -> list[Case]:
    """`sample` is the stratified 100 the build was first tuned on, `dev` the 250 fixes are found on, `test` the held-out 900,
    `test-<N>` a committed stratified draw of it, and `all` every case, which is only worth running without the model."""
    if split == "sample":
        return sample(cache=cache)
    if split == "dev":
        return dev(cache)
    if split == "test":
        return held_out(cache)
    if split.startswith("test-"):
        return draw(int(split[5:]), cache)
    if split == "all":
        return every(cache)
    raise ValueError(f"unknown VisEval split {split!r}; one of {SPLITS}")


def register(cases: list[Case], cache: Path = CACHE) -> Path:
    return tables.save_registry(cache / "registry", {name: build_database(name, cache) for name in sorted({c.database for c in cases})}, DESCRIPTION)


def score(case: Case, result, spec: ChartSpec | None, vega_lite: dict | None) -> dict:
    """VisEval's three legality checks over what our spec drew, each with its reason kept when it fails, and the gold chart
    as the facet the table groups by."""
    info = chart_info(vega_lite, result) if vega_lite is not None and isinstance(result, AnalysisResult) else None
    return judge(case.extra, info) | {"facets": {"gold chart": case.extra["chart"]}}


def judge(gold: dict, info: dict | None) -> dict:
    """`order` is None where the case asks for no order, so the metric is reported over the cases that ask. Their harness runs
    it only after `data` passes; here it is judged on the channels as drawn when `data` failed, so the two rows stay separable."""
    asks = gold["vis_obj"]["sort"] is not None
    if info is None:
        return {"chart": False, "data": False, "order": False if asks else None, "reasons": {}}
    verdicts = {"chart": published.chart_check(info, gold["chart"], bool(gold["stacked_bar"])),
                "data": _their(published.data_check, info, gold["vis_obj"], gold["channel_specified"])}
    if asks:
        info.setdefault("channel_map", IDENTITY)
        verdicts["order"] = _their(published.order_check, info, gold["vis_obj"], gold["sort_by"])
    rows = {name: passed for name, (passed, _) in verdicts.items()}
    return rows | {"order": rows.get("order"), "reasons": {name: why for name, (passed, why) in verdicts.items() if not passed}}


IDENTITY = {"x": "x", "y": "y", "classify": "classify"}


def _their(check, *args) -> tuple[bool, str]:
    try:
        return check(*args)
    except Exception as e:  # their code assumes a chart their own harness drew; a shape it never met is a miss, not a crash
        return False, f"{type(e).__name__}: {e}"


def dataset(cache: Path = CACHE) -> Path:
    """The 19MB dataset, downloaded once. The only step that needs the network."""
    root = cache / "visEval_dataset"
    if not root.exists():
        cache.mkdir(parents=True, exist_ok=True)
        archive = cache / "viseval.zip"
        if not archive.exists():
            urllib.request.urlretrieve(URL, archive)
        with zipfile.ZipFile(archive) as z:
            z.extractall(cache)
    return root


def golden_table(vis_obj: dict) -> QueryResult:
    """The ground-truth rows, one column per channel.

    Two shapes hide in the multi-series cases and telling them apart is the difference between scoring the agent and scoring
    the reader of the file: a grouped scatter carries its own x values per series, while a grouped line or a stacked bar shares
    one axis and pads the grid with zeros where a combination has no row at all. A database cannot return a padded zero, so on
    the shared-axis shape those cells are dropped rather than counted against the answer.
    """
    xs, ys, series = vis_obj["x_data"], vis_obj["y_data"], vis_obj["classify"]
    if not series:
        return QueryResult(columns=[vis_obj["x_name"], vis_obj["y_name"]], rows=[[x, y] for x, y in zip(xs[0], ys[0])])
    own_x = len(xs) == len(ys)
    rows = [[x, name, y] for i, name in enumerate(series) for x, y in zip(xs[i] if own_x else xs[0], ys[i]) if own_x or y]
    return QueryResult(columns=[vis_obj["x_name"], "series", vis_obj["y_name"]], rows=rows)


def sample(quota: dict[str, int] = QUOTA, seed: int = SEED, cache: Path = CACHE) -> list[Case]:
    """Stratified by chart type, then by difficulty inside it, from one committed seed."""
    raw = json.loads((dataset(cache) / "visEval.json").read_text())
    chosen, rng = [], random.Random(seed)
    for chart, wanted in quota.items():
        pool = sorted((k for k, v in raw.items() if v["chart"] == chart), key=lambda k: (raw[k]["hardness"], k))
        chosen += [_case(key, raw[key], cache) for key in rng.sample(pool, min(wanted, len(pool)))]
    return sorted(chosen, key=lambda c: c.id)


def dev(cache: Path = CACHE) -> list[Case]:
    """The cases fixes are found on. Drawn once as the stratified 100 that has already been tuned against, plus a further
    stratified draw up to 250 — so everything that shaped this build is inside dev, and the held-out set stays clean."""
    ids = set(DEV.read_text().split())
    return [c for c in every(cache) if c.id in ids]


def held_out(cache: Path = CACHE) -> list[Case]:
    """Everything dev is not. Run rarely, never tuned against, and every number from it says which set it came from."""
    ids = set(DEV.read_text().split())
    return [c for c in every(cache) if c.id not in ids]


def draw(size: int, cache: Path = CACHE) -> list[Case]:
    """A stratified subsample of the held-out set, committed by id exactly as dev is. A held-out set is a population to draw
    from, not a quantity to spend: 250 of the 900 leaves 650 never looked at for a second question. The draw keeps dev's quota
    shape, so the held-out number is comparable to the dev number it checks."""
    pool = held_out(cache)
    path = DRAWS / f"test-{size}.txt"
    if path.exists():  # the committed file is the authority from then on, for the same reason dev.txt is
        ids = set(path.read_text().split())
        return [c for c in pool if c.id in ids]
    rng, chosen = random.Random(SEED), []
    for chart, share in DEV_QUOTA.items():
        wanted = round(size * share / sum(DEV_QUOTA.values()))
        available = sorted((c for c in pool if c.extra["chart"] == chart), key=lambda c: (c.hardness, c.id))
        chosen += rng.sample(available, min(wanted, len(available)))
    path.write_text("\n".join(sorted(c.id for c in chosen)) + "\n")
    return sorted(chosen, key=lambda c: c.id)


def draw_dev(cache: Path = CACHE) -> list[str]:
    """Write `dev.txt`. Run once, by hand; from then on the committed file is the authority, because a later change to the
    quota or the seed would otherwise move cases across the line without anyone noticing."""
    raw = json.loads((dataset(cache) / "visEval.json").read_text())
    chosen, rng = {c.id for c in sample(cache=cache)}, random.Random(SEED)
    for chart, wanted in DEV_QUOTA.items():
        pool = sorted((k for k, v in raw.items() if v["chart"] == chart and k not in chosen), key=lambda k: (raw[k]["hardness"], k))
        chosen |= set(rng.sample(pool, min(wanted - QUOTA[chart], len(pool))))
    return sorted(chosen)


@functools.cache
def every(cache: Path = CACHE) -> list[Case]:
    """All 1,150 of them, with each gold query's rows fetched once per process. Only worth running live in part: a deterministic
    sweep over every shape has no sampling error and no run-to-run noise, so it measures a renderer change exactly."""
    raw = json.loads((dataset(cache) / "visEval.json").read_text())
    return sorted((_case(key, case, cache) for key, case in raw.items()), key=lambda c: c.id)


def _case(key: str, case: dict, cache: Path) -> Case:
    """`vis_obj`'s pre-baked `x_data`/`y_data` are shaped by the chart type, so reading them back as a table scores the reader of
    the file as much as the agent; the gold SQL's own rows are the ground truth for `ex` and `correct`. All 1,150 cases ship
    gold SQL and 1,002 of them have no binning, so they run through the same read-only handle and guard the agent's own SQL
    passes. A binned case's `BIN x BY` clause is not SQL, so it ships no query that runs and is scored on `data` alone.
    """
    data, meta, mark = case["vis_query"]["data_part"], case["query_meta"][0], FORMS[case["chart"]]
    gold_sql = "" if data["binning"] else data["sql_part"]
    extra = {"chart": case["chart"], "mark": mark[0], "stacked": mark[1], "golden": golden_table(case["vis_obj"]),
             "binning": data["binning"], "vis_obj": case["vis_obj"], "channel_specified": meta.get("channel_specified", []),
             "sort_by": meta.get("sort_by"), "stacked_bar": meta.get("stacked_bar")}
    return Case(id=key, dataset="viseval", database=case["db_id"], question=case["nl_queries"][0], paraphrases=case["nl_queries"][1:],
                gold_sql=gold_sql, gold_rows=gold_result(case["db_id"], gold_sql, cache), hardness=case["hardness"], extra=extra)


def gold_result(database: str, gold_sql: str, cache: Path = CACHE) -> QueryResult | None:
    """None where there is no query, or where the gold does not run: those are counted, never scored."""
    if not gold_sql:
        return None
    db = ReadOnlyDatabase(build_database(database, cache))
    try:
        return db.execute(gold_sql)
    except (SqlRejected, QueryFailed):
        return None
    finally:
        db.close()


def build_database(db_id: str, cache: Path = CACHE) -> Path:
    """One SQLite file from VisEval's folder of CSVs."""
    return tables.build(cache / "databases" / f"{db_id}.db", list((dataset(cache) / "databases" / db_id).glob("*.csv")))


# Our ChartType name for the Vega mark a gold chart name means. Vocabulary only: `arc` is the mark the renderer draws and `pie`
# is what a ChartSpec calls it, the same way `rect` is drawn for `heatmap`.
ASKS = {"arc": "pie"}
DIRECTIONS = {"ascending": "asc", "descending": "desc"}


def dry(cases: list[Case]) -> list[dict]:
    """No model and no database: the ground-truth table straight into the renderer, to see which form the rules give it.

    The spec is the ground truth's own: its channels, and the order the case states. So `order` here is a **ceiling** — it
    asks whether the renderer draws an order it was given, not whether the model read that order out of the question, which
    only a live run can say. `ex` and `correct` are not questions a dry sweep can ask, because the table charted *is* the
    ground truth, so they are left unscored rather than reported as numbers that mean nothing.
    """
    scored = []
    for case in cases:
        table, gold = case.extra["golden"], case.extra
        if not table.rows or field_type([row[-1] for row in table.rows]) != "quantitative":
            continue
        spec = ChartSpec(chart_type=ASKS.get(gold["mark"], gold["mark"]), x=table.columns[0], y=table.columns[-1], sort=_asked(case),
                         color="series" if len(table.columns) > 2 else None, stacked=gold["stacked"], title=case.question)
        result = AnalysisResult(intent="comparison", sql="", narrative="", table=table)
        vega_lite = renderer.build(spec, result, case.database)
        scored.append(core(case, result, vega_lite) | score(case, result, spec, vega_lite)
                      | {"ex": None, "correct": None, "why": chartable.unreadable(vega_lite), "spec": spec.model_dump()})
    return scored


def _asked(case: Case) -> str | None:
    """The order the case states, in a ChartSpec's words, for the dry sweep, which has no model to read it out of the question."""
    sort = case.extra["vis_obj"]["sort"]
    if not sort or sort["order"] not in DIRECTIONS:
        return None
    return f"{'category' if sort['channel'] == 'x' else 'measure'} {DIRECTIONS[sort['order']]}"
