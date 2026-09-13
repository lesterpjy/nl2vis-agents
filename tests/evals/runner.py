"""Run the tier 2 dataset live (recording tapes) or from tapes; score correctness, intent, Clarification, chart; keep tokens and cost per case."""

import argparse
import math
import os
import tempfile
from dataclasses import dataclass
from itertools import permutations
from pathlib import Path

from pydantic_evals import increment_eval_metric, set_eval_attribute
from pydantic_evals.evaluators import Evaluator, EvaluatorContext
from pydantic_evals.reporting import EvaluationReport

from data_agents import orchestrator
from data_agents.charts import chartable, renderer
from data_agents.contracts import AnalysisResult, Clarification, QueryResult, Unanswerable
from data_agents.data import registry
from data_agents.system.sessions import SessionStore
from tests.evals.cases import EvalInputs, EvalOutput, dataset
from tests.evals.tape import recording, replaying

TAPES = Path(__file__).parent / "recordings"
USER = "admin"  # the seed admin holds every database by role; the eval is about SQL, not authorization


def same(a, b) -> bool:
    if isinstance(a, (int, float)) or isinstance(b, (int, float)):
        try:
            return math.isclose(float(a), float(b), rel_tol=1e-4, abs_tol=0.01)
        except (TypeError, ValueError):
            return False
    return str(a).strip().lower() == str(b).strip().lower()


def same_result_set(agent: QueryResult, golden: QueryResult) -> bool:
    """Unordered row equality with numeric tolerance. Extra agent columns are tolerated, extra or missing rows are not."""
    if len(agent.rows) != len(golden.rows) or len(agent.columns) < len(golden.columns):
        return False
    for mapping in permutations(range(len(agent.columns)), len(golden.columns)):
        pool = list(agent.rows)
        for g in golden.rows:
            hit = next((r for r in pool if all(same(r[k], v) for k, v in zip(mapping, g))), None)
            if hit is None:
                break
            pool.remove(hit)
        else:
            return True
    return False


@dataclass
class Correct(Evaluator[EvalInputs, EvalOutput, None]):
    def evaluate(self, ctx: EvaluatorContext[EvalInputs, EvalOutput, None]) -> bool:
        if not ctx.expected_output.sql:  # abstention case: refusing is the right answer, querying is not
            return isinstance(ctx.output.result, Unanswerable)
        if not isinstance(ctx.output.result, AnalysisResult):
            return False
        db = registry.open_database(registry.load()[ctx.inputs.database])
        try:
            return same_result_set(ctx.output.result.table, db.execute(ctx.expected_output.sql))
        finally:
            db.close()


@dataclass
class IntentLabelled(Evaluator[EvalInputs, EvalOutput, None]):
    def evaluate(self, ctx: EvaluatorContext[EvalInputs, EvalOutput, None]) -> bool:
        return not ctx.expected_output.intents or (isinstance(ctx.output.result, AnalysisResult) and ctx.output.result.intent in ctx.expected_output.intents)


@dataclass
class ClarifiedRight(Evaluator[EvalInputs, EvalOutput, None]):
    """Asks exactly when expected: first Turn of a clarify case, never otherwise."""

    def evaluate(self, ctx: EvaluatorContext[EvalInputs, EvalOutput, None]) -> bool:
        expected = [ctx.expected_output.clarify] + [False] * (len(ctx.inputs.turns) - 1)
        return ctx.output.asked == expected


@dataclass
class ChartedRight(Evaluator[EvalInputs, EvalOutput, None]):
    def evaluate(self, ctx: EvaluatorContext[EvalInputs, EvalOutput, None]) -> bool:
        return ctx.output.charted == ctx.expected_output.chart


@dataclass
class ChartableShape(Evaluator[EvalInputs, EvalOutput, None]):
    """One column names each row, so the chart side never has to pick one fragment of the row's identity as the axis."""

    def evaluate(self, ctx: EvaluatorContext[EvalInputs, EvalOutput, None]) -> bool:
        return not isinstance(ctx.output.result, AnalysisResult) or not chartable.split_identity(ctx.output.result.table)


@dataclass
class ChartReads(Evaluator[EvalInputs, EvalOutput, None]):
    """The chart that was actually built has no legend with an entry per row, no empty series slots, no repeated axis labels."""

    def evaluate(self, ctx: EvaluatorContext[EvalInputs, EvalOutput, None]) -> bool:
        if ctx.output.spec is None or not isinstance(ctx.output.result, AnalysisResult):
            return True  # nothing was charted; ChartedRight is the assertion that cares
        return chartable.unreadable(renderer.build(ctx.output.spec, ctx.output.result, ctx.inputs.database)) is None


dataset.evaluators = [Correct(), IntentLabelled(), ClarifiedRight(), ChartedRight(), ChartableShape(), ChartReads()]


def run_case(inputs: EvalInputs, work_dir: Path) -> EvalOutput:
    store = SessionStore(work_dir / f"{inputs.name}.db")
    session = store.create(USER, inputs.database)
    asked, metrics, charted, spec = [], [], False, None
    for question in inputs.turns:
        events = []
        result = orchestrator.run_turn(store, session, question, events.append, chart=True)
        asked.append(isinstance(result, Clarification))
        metrics.append(events[-1].metrics)
        charted = events[-1].charted
        spec = next((e.spec for e in events if e.kind == "chart_ready"), None)
    for m in metrics:
        for key in ("input_tokens", "cache_read_tokens", "output_tokens"):
            increment_eval_metric(key, getattr(m, key))
        increment_eval_metric("cost_usd", m.cost_usd or 0.0)
        increment_eval_metric("model_ms", sum(ms for stage, ms in m.latency_ms.items() if stage in ("analysis", "visualization")))
    if isinstance(result, AnalysisResult):
        set_eval_attribute("sql", result.sql)
    return EvalOutput(result=result, asked=asked, charted=charted, spec=spec, metrics=metrics)


def evaluate(live: bool, tapes: Path = TAPES, names: list[str] | None = None, label: str | None = None) -> EvaluationReport:
    work_dir = Path(tempfile.mkdtemp(prefix="data-agents-evals-"))
    cases = dataset if names is None else dataset.model_copy(update={"cases": [c for c in dataset.cases if c.name in names]})

    def task(inputs: EvalInputs) -> EvalOutput:
        with (recording if live else replaying)(tapes / f"{inputs.name}.json"):
            return run_case(inputs, work_dir)

    # One case at a time: the recorder overrides the agents' model process-wide, and prefix-cache numbers stay readable.
    return cases.evaluate_sync(task, name=label or ("live" if live else "replay"), max_concurrency=1, progress=False)


def markdown_table(report: EvaluationReport) -> str:
    lines = ["| case | correct | intent | clarify | chart | shape | reads | tokens in | cached | out | cost | model s |",
             "|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for c in report.cases:
        marks = ["✓" if c.assertions[k].value else "✗" for k in ("Correct", "IntentLabelled", "ClarifiedRight", "ChartedRight", "ChartableShape", "ChartReads")]
        m = {k: c.metrics.get(k, 0) for k in ("input_tokens", "cache_read_tokens", "output_tokens", "cost_usd", "model_ms")}  # a zero increment records nothing
        lines.append(f"| {c.name} | {' | '.join(marks)} | {m['input_tokens']:.0f} | {m['cache_read_tokens']:.0f} | {m['output_tokens']:.0f} | ${m['cost_usd']:.4f} | {m['model_ms'] / 1000:.1f} |")
    n = len(report.cases)
    totals = {k: sum(c.metrics.get(k, 0) for c in report.cases) for k in ("input_tokens", "cache_read_tokens", "output_tokens", "cost_usd", "model_ms")}
    correct = sum(c.assertions["Correct"].value for c in report.cases)
    lines.append(f"| **total ({correct}/{n} correct)** | | | | | | | {totals['input_tokens']:.0f} | {totals['cache_read_tokens']:.0f} | {totals['output_tokens']:.0f} "
                 f"| ${totals['cost_usd']:.4f} | {totals['model_ms'] / 1000:.1f} |")
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="Tier 2 evals: replay by default; --live calls the model and records tapes into --tapes.")
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--tapes", type=Path, default=TAPES)
    parser.add_argument("--variant", default=os.environ.get("SCHEMA_VARIANT", "lean"), help="Schema Document variant: full, no_examples, or lean")
    parser.add_argument("--case", action="append", dest="names")
    args = parser.parse_args()
    os.environ["SCHEMA_VARIANT"] = args.variant
    report = evaluate(args.live, args.tapes, args.names, label=f"{'live' if args.live else 'replay'}/{args.variant}")
    report.print(include_averages=False, include_durations=False)
    print(markdown_table(report))


if __name__ == "__main__":
    main()
