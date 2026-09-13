"""Run a benchmark's cases through both agents and score them; one loop for every dataset.

The dataset is an adapter module under `tests/bench/` with three names: `cases(split)`, `register(cases)` and
`score(case, result, spec, vega_lite)`. Nothing here knows what a dataset's gold looks like: the core rows come from
`scoring`, the dataset's own rows from its `score`, and a Clarification is answered by the simulated user (`user.py`) as a
second Turn of the same Session, scored as the answer the user ends with beside the first pass, never in its place.
"""

import argparse
import importlib
import json
import os
import time
import traceback
from pathlib import Path

# The benchmark databases live in a registry of their own, and `registry` and `auth` both read the directory once, at import.
# So it is named here, before they are imported, rather than patched afterwards where the default argument has already bound.
BENCH = Path(os.environ.get("BENCH_DIR", ".bench"))
os.environ["REGISTRY_DIR"] = str(BENCH / "registry")

from data_agents import orchestrator  # noqa: E402
from data_agents.charts import renderer  # noqa: E402
from data_agents.contracts import AnalysisResult, Clarification  # noqa: E402
from data_agents.data import registry, schema_document  # noqa: E402
from data_agents.system.sessions import SessionStore  # noqa: E402
from tests.bench import user  # noqa: E402
from tests.bench.case import Case  # noqa: E402
from tests.bench.scoring import COSTS, asked, score  # noqa: E402
from tests.bench.table import table  # noqa: E402

USER = "admin"


def adapter_named(name: str):
    return importlib.import_module(f"tests.bench.{name}")


def run(adapter, cases: list[Case], work: Path, out: Path | None = None, reply: bool = True) -> list[dict]:
    """`out` is rewritten after every case, not once at the end: a live run is the most expensive thing here, and one hung
    model call once threw away 151 finished cases held in memory. Rewriting a 1MB file 250 times is nothing against that."""
    adapter.register(cases)  # build the SQLite files and the Schema Documents the Analysis Agent will read
    store = SessionStore(work / "bench-sessions.db")
    scored = []
    for i, case in enumerate(cases, 1):
        scored.append(play(adapter, store, store.create(USER, case.database), case, reply=reply))
        if out:
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(json.dumps(scored, indent=1))
        print(f"{i:>3}/{len(cases)} {line(case, scored[-1])}", flush=True)
    return scored


def play(adapter, store: SessionStore, session, case: Case, row: dict | None = None, reply: bool = True) -> dict:
    """One case through its Session: the question, then, when the Turn ends in a Clarification, the simulated user's reply as
    a second Turn, whose rows go under `after`. `row` is a first Turn already played and stored, so a reply can be paid for
    without paying for the question again (`reply.py`), which is also what `reply=False` leaves for later."""
    if row is None:
        row = turn(adapter, store, session, case, case.question)
    if "error" in row or not asked(row["events"]) or not reply:
        return row | {"asked": asked(row["events"]), "answered": None if "error" in row else False if asked(row["events"]) else None}
    try:
        words = answer(store, session, case)
    except Exception as e:  # the user's own model call, outside any Turn: the first pass was paid for and is kept
        return row | {"asked": True, "answered": False, "reply_error": f"{type(e).__name__}: {e}"}
    later = turn(adapter, store, session, case, words.output)
    after = {k: v for k, v in later.items() if k not in COSTS and k not in ("events", "answer")}
    costs = {k: row.get(k, 0) + later.get(k, 0) for k in COSTS}  # what the case cost, over both Turns
    usage = words.usage() if callable(words.usage) else words.usage
    costs["requests"] += usage.requests
    costs["cost_usd"] += _cost(usage)
    return row | costs | {"asked": True, "answered": True, "after": after, "answer": later.get("answer"),
                         "events": row["events"] + later["events"], "reply": words.output}


def answer(store: SessionStore, session, case: Case):
    """What the simulated user is given: the question, the analyst's question back, the Session's last answer if it holds one,
    and what the database is about. Nothing of the case's gold."""
    clarification = store.last_turn(session.id).result
    assert isinstance(clarification, Clarification)
    previous = [t.result for t in store.turns_of(session.id) if isinstance(t.result, AnalysisResult)]
    narrative, columns = (previous[-1].narrative, previous[-1].table.columns) if previous else (None, None)
    description, tables = about(case.database)
    return user.reply(case.question, clarification.question, description, tables, narrative, columns)


def about(database: str) -> tuple[str, list[str]]:
    entry = registry.load()[database]
    db = registry.open_database(entry)
    try:
        return entry.description, [t.name for t in schema_document.introspect(db)]
    finally:
        db.close()


def turn(adapter, store: SessionStore, session, case: Case, question: str) -> dict:
    """One Turn, scored: the row a run keeps. **Any** failure is one failed case with its trace, never a stopped run — a Turn
    that errored is exactly where the correction layers are most worth reading, and a scorer that crashes on a shape it has
    never met must not throw away the cases already paid for. What it cost is read off the stream either way."""
    events: list = []
    started = time.perf_counter()
    try:
        result = orchestrator.run_turn(store, session, question, events.append, chart=True)
        ready = next((e for e in events if e.kind == "chart_ready"), None)
        built = renderer.build(ready.spec, result, case.database) if ready else None
        # the answer is kept so the reducer can read the final table, and the stream so the correction layers are scored from this run
        row = score(adapter, case, result, ready.spec if ready else None, built) | {
            "answer": result.table.model_dump() if isinstance(result, AnalysisResult) else None}
    except Exception as e:
        row = _failed(adapter, case, e, events)
    return row | _spent(events, started) | {"events": [e.model_dump() for e in events]}


def _spent(events: list, started: float) -> dict:
    """What the case cost, off the closing metrics where there are any and off the model calls otherwise: a Turn that errored
    is often the expensive one — a request-limit loop is eight calls — and a run whose spend omits them under-reports itself."""
    finished = next((e for e in reversed(events) if e.kind == "turn_finished"), None)
    seconds = time.perf_counter() - started
    if finished is not None:
        return {"cost_usd": finished.metrics.cost_usd or 0.0, "requests": finished.metrics.requests, "seconds": seconds}
    calls = [e for e in events if e.kind == "model_call_done"]
    return {"cost_usd": sum(e.cost_usd or 0.0 for e in calls), "requests": sum(e.requests for e in calls), "seconds": seconds}


def _failed(adapter, case: Case, e: Exception, events: list) -> dict:
    """A failure kept in a shape a bug hunt can read: the exception's type and message, the traceback, and where it happened —
    a stage the orchestrator reported, or `harness` for a fault in the scoring and charting this module does after the Turn."""
    stage = next((event.stage for event in reversed(events) if event.kind == "turn_error"), "harness")
    trace = "".join(traceback.format_exception(type(e), e, e.__traceback__))
    try:
        scored = score(adapter, case, None, None, None)
    except Exception:  # the scorer is what failed, so it cannot describe this case either; the case is still a row
        scored = {"id": case.id, "dataset": case.dataset, "database": case.database, "hardness": case.hardness}
    return scored | {"error": f"{type(e).__name__}: {e}", "error_stage": stage, "traceback": trace[-2000:], "answer": None}


def _cost(usage) -> float:
    return float(usage.cost) if getattr(usage, "cost", None) is not None else 0.0


def line(case: Case, row: dict) -> str:
    if "error" in row:
        return f"{case.id:<22} ERROR {row['error']}"
    path = " asked, answered" if row.get("answered") else " asked" if row.get("asked") else ""
    final = row.get("after") or row
    return (f"{case.id:<22} correct={final['correct']!s:<5} charted={final['charted']!s:<5} reads={final['reads']!s:<5} "
            f"mark={final['mark']} ${row.get('cost_usd', 0):.4f}{path}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Benchmark runner: --dry costs nothing where the dataset offers it, a live run costs about a cent per case.")
    parser.add_argument("--dataset", required=True, help="the adapter module under tests/bench/")
    parser.add_argument("--split", default="sample", help="the dataset's own name for a split; test splits need --final")
    parser.add_argument("--dry", action="store_true", help="the ground-truth tables through the renderer, no model; only where the dataset offers one")
    parser.add_argument("--final", action="store_true", help="means it: confirms a held-out run")
    parser.add_argument("--blind", action="store_true", help="withhold the question from the Visualization Agent: `chart` then measures inference, not instruction-following")
    parser.add_argument("--no-reply", action="store_true", help="leave every Clarification unanswered; the replies can be played later from the stored Sessions with reply.py")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--case", action="append", dest="ids", help="only these case ids")
    parser.add_argument("--out", type=Path, default=BENCH / "results.json")
    args = parser.parse_args()
    if args.blind:
        os.environ["VISUALIZATION_SEES_QUESTION"] = "0"
    # The held-out set is the only number that generalises, and it stops being one the moment it is run casually.
    if args.split.startswith("test") and not args.final and not args.dry:
        parser.error("a test split is held out: fixes are found on dev, and this is run at most once more. Add --final to mean it.")
    adapter = adapter_named(args.dataset)
    if args.dry and not hasattr(adapter, "dry"):
        parser.error(f"{args.dataset} has no dry sweep: its gold carries no result table to chart")
    cases = [c for c in adapter.cases(args.split) if not args.ids or c.id in args.ids][: args.limit]
    # The guard is an acknowledgement, not a ceiling: naming the size with --limit is how a big run is asked for, which is what
    # the message has always said. Applying --limit before this test made it a 300-case cap that no flag could satisfy.
    if not args.dry and len(cases) > 300 and args.limit is None:
        parser.error(f"{len(cases)} cases live would cost about ${0.01 * len(cases):.0f}; ask for it with --limit")
    scored = adapter.dry(cases) if args.dry else run(adapter, cases, BENCH, args.out, reply=not args.no_reply)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(scored, indent=1))
    print(table(scored, f"{args.dataset} {args.split}{' dry' if args.dry else ''}"))


if __name__ == "__main__":
    main()
