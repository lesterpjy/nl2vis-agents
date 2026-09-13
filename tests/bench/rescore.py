"""Score a stored benchmark run again, with no model call.

`runner.run` keeps each Turn's whole event stream for exactly this. A change to the scoring rules is a change to how the same
answers are *read*, so re-running the model to measure it would price the model's nondeterminism into a deterministic change
and cost a dollar for the privilege. Every number computed under an older rule is re-stated from here.

The result and the spec are both on the stream, so the Vega-Lite is rebuilt rather than stored: the renderer is deterministic,
and rebuilding is what lets a new rule ask what the bars actually did. A case the simulated user answered holds two Turns on
one stream; the first is the first pass and the last is `after`.
"""

import argparse
import json
from pathlib import Path

from data_agents.charts import renderer
from data_agents.contracts import AnalysisResult, ChartSpec, Clarification, Unanswerable
from tests.bench.case import Case
from tests.bench.paired import report
from tests.bench.runner import adapter_named
from tests.bench.scoring import COSTS, asked, score
from tests.bench.table import table

KEPT = COSTS + ("answer", "events", "error", "error_stage", "traceback", "reply", "reply_error")  # what the run paid for and what it caught; every score is recomputed


def rescore(rows: list[dict], adapter, cases: dict[str, Case]) -> list[dict]:
    out = []
    for row in rows:
        case, played = cases[row["id"]], turns(row.get("events", []))
        first = {k: row[k] for k in KEPT if k in row} | scored(adapter, case, played[0] if played else [])
        first |= {"asked": asked(first["events"]), "answered": None}
        if first["asked"]:
            first["answered"] = len(played) > 1
        if first["answered"]:
            first["after"] = scored(adapter, case, played[-1])
        out.append(first)
    return out


def turns(events: list[dict]) -> list[list[dict]]:
    """The stream cut into Turns, each ending at its `turn_finished`; a tail with none is a Turn that errored."""
    out, current = [], []
    for event in events:
        current.append(event)
        if event["kind"] == "turn_finished":
            out.append(current)
            current = []
    return out + ([current] if current else [])


def scored(adapter, case: Case, events: list[dict]) -> dict:
    result, spec = _result(events), _spec(events)
    vega_lite = None
    if isinstance(result, AnalysisResult) and spec is not None:
        try:
            vega_lite = renderer.build(spec, result, case.database)
        except ValueError:  # the live run would have failed here too, and did: the row keeps its error
            pass
    return score(adapter, case, result, spec, vega_lite)


def _result(events: list[dict]) -> AnalysisResult | Clarification | Unanswerable | None:
    """What the Turn ended in, off its `turn_finished` event; an AnalysisResult carries the intent the form layer reads."""
    finished = next((e["result"] for e in events if e["kind"] == "turn_finished"), None)
    if finished is None:
        return None
    return AnalysisResult(**finished) if "table" in finished else Clarification(**finished) if "question" in finished else Unanswerable(**finished)


def _spec(events: list[dict]) -> ChartSpec | None:
    ready = next((e for e in events if e["kind"] == "chart_ready"), None)
    return ChartSpec(**ready["spec"]) if ready else None


def main() -> None:
    parser = argparse.ArgumentParser(description="Re-score a stored run under the current rules, without the model.")
    parser.add_argument("results", type=Path)
    parser.add_argument("--dataset", required=True, help="the adapter module under tests/bench/")
    parser.add_argument("--out", type=Path, default=None)
    parser.add_argument("--which", default="rescored")
    args = parser.parse_args()
    adapter = adapter_named(args.dataset)
    before = json.loads(args.results.read_text())
    after = rescore(before, adapter, {c.id: c for c in adapter.cases("all")})
    if args.out:
        args.out.write_text(json.dumps(after, indent=1))
    print(table(after, args.which))
    print("\n" + report(before, after, ("old scoring", "new scoring")))


if __name__ == "__main__":
    main()
