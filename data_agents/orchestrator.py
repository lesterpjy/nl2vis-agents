"""Run one Turn: authorize, hand the Analysis Agent one database handle, optionally chart, persist, emit events. Plain code, no LLM routing."""

import os
import time
from contextlib import contextmanager
from typing import Callable

import logfire
from pydantic import ValidationError
from pydantic_ai.exceptions import AgentRunError, ModelAPIError, UsageLimitExceeded
from pydantic_ai.usage import RunUsage

from data_agents.agents import router
from data_agents.charts import chartable, renderer
from data_agents.contracts import AnalysisResult, ChartHints, ChartReady, ChartSkipped, ChartSpec, Clarification, ModelCallDone, PlanChosen, ReshapeRequest, ReshapeRequested, RowsFetched, StageStarted, TurnError, TurnEvent, TurnFinished, TurnMetrics, TurnPlan, Unanswerable
from data_agents.data import registry, schema_document
from data_agents.system import auth
from data_agents.agents.analysis import AnalysisDeps, run_analysis
from data_agents.agents.visualization import run_visualization
from data_agents.system.sessions import Session, SessionStore, StoredChart

MAX_TURNS_PER_SESSION = 20  # the outermost of the limits that bound what one Session can spend
Emit = Callable[[TurnEvent], None]


def run_turn(store: SessionStore, session: Session, question: str, emit: Emit, chart: bool = False,
             hints: ChartHints | None = None) -> AnalysisResult | Clarification | Unanswerable:
    stage = ["authorize"]  # the stage that was running when an error is raised
    try:
        with logfire.span("turn {session_id}: {question}", session_id=session.id, question=question, database=session.database, user=session.user):
            return _run_turn(store, session, question, emit, chart, stage, hints)
    except Exception as e:
        emit(TurnError(stage=stage[0], message=plain(e), detail=str(e)))
        raise


def plain(e: Exception) -> str:
    """What a user reads when a Turn fails: a sentence with a next move, never the exception's own text. Our own
    exceptions are already sentences; the framework's and the network's are translated; anything else is named as ours."""
    if isinstance(e, UsageLimitExceeded):
        return "The question took too many steps to answer; try a narrower one."
    if isinstance(e, ModelAPIError):
        return "The model service did not answer; try again in a moment."
    if isinstance(e, AgentRunError):  # the framework's own RuntimeErrors: retries exhausted, a cut-off tool call, a content filter
        return "The model could not produce an answer it was allowed to give; try rephrasing the question."
    if isinstance(e, (KeyError, RuntimeError, ValueError, PermissionError)) and not isinstance(e, ValidationError):  # ours, with a sentence in them
        return str(e.args[0]) if e.args else str(e)
    if "timeout" in type(e).__name__.lower() or "timed out" in str(e).lower():
        return "The model did not answer in time; try again."
    return "Something went wrong inside this Turn; try again, or ask a narrower question."


def visualize_turn(store: SessionStore, session: Session, emit: Emit, hints: ChartHints | None = None) -> StoredChart | None:
    """Chart the Session's latest AnalysisResult without a new analysis, recorded as a Turn so the chart is kept like any other."""
    result = store.last_result(session.id)
    question = "Chart the previous result" + (hints.as_prompt().replace("\n\nUser preferences: ", ", ") if hints else "")
    keep = lambda drawn, metrics: store.record_turn(session, question, None, result, metrics, drawn)  # the model was asked nothing new
    return chart_result(session.database, result, emit, hints, keep)


def chart_result(source: str, result: AnalysisResult, emit: Emit, hints: ChartHints | None = None,
                 keep: Callable[[StoredChart | None, TurnMetrics], None] = lambda drawn, metrics: None) -> StoredChart | None:
    """One chart and no new analysis: a Session's latest result, or data the user supplied as SQL or CSV.

    No question reaches the Visualization Agent here: the words that asked for this table were spoken in an earlier Turn, or
    never — the user brought the data. Only the hints speak for them. `keep` runs before the Turn is announced finished.
    """
    latency, usage = {}, RunUsage()
    try:
        drawn, _ = _chart(source, result, emit, latency, usage, None, hints)
    except Exception as e:
        emit(TurnError(stage="visualization", message=plain(e), detail=str(e)))
        raise
    metrics = _metrics(latency, usage)
    keep(drawn, metrics)
    emit(TurnFinished(result=result, charted=drawn is not None, metrics=metrics))
    return drawn


def backfill_charts(store: SessionStore, emit: Emit) -> int:
    """Draw the chart of every answered Turn that kept none, from its own question and result, as the Turn would have.

    For history recorded before Chart Specs were kept. The Visualization Agent sees exactly what it saw then, the question
    and the result, so this is the original decision made late — not a re-derivation without the question, which invents a
    different chart and calls it the old one. One model call per Turn; a Turn the agent cannot chart is left as it is and said so.
    """
    drawn = 0
    for session, turn in store.turns_without_charts():
        emit(StageStarted(stage=f"{session.id} turn {turn.n}: {turn.question}"))
        latency, usage = {}, RunUsage()
        try:
            kept, _ = _chart(session.database, turn.result, emit, latency, usage, turn.question)
        except Exception as e:
            emit(TurnError(stage="visualization", message=f"{session.id} turn {turn.n}: {plain(e)}", detail=str(e)))
            continue
        if kept:
            store.attach_chart(session.id, turn.n, kept)
            drawn += 1
    return drawn


def _run_turn(store: SessionStore, session: Session, question: str, emit: Emit, chart: bool, stage: list[str], hints: ChartHints | None):
    latency: dict[str, float] = {"sql": 0.0}
    start = time.perf_counter()
    emit(StageStarted(stage="authorize"))
    entries = registry.load()
    user = auth.user(session.user)
    auth.authorize(user, session.database, entries)  # grants ∩ live Registry, on every Turn
    if session.turns >= MAX_TURNS_PER_SESSION:
        raise RuntimeError(f"session {session.id} reached its {MAX_TURNS_PER_SESSION}-turn cap")
    entry = entries[session.database]
    db = registry.open_database(entry)
    latency["authorize"] = (time.perf_counter() - start) * 1000

    def emit_and_time(event: TurnEvent) -> None:
        if isinstance(event, RowsFetched):
            latency["sql"] += event.latency_ms
        emit(event)

    usage = RunUsage()
    try:  # the handle stays open through the chart stage: a reshape asks the Analysis Agent one more question
        # A follow-up to an answer is routed: plain code decides whether the router may run, the router decides which
        # agent runs, and a present_only plan charts the stored result with the user's words as the question. A reshape
        # request on that path means the words wanted new data after all, so the analysis runs as if the router had said so.
        if (last := store.last_turn(session.id)) and isinstance(last.result, AnalysisResult):
            stage[0] = "router"
            if _plan(question, last, emit, latency, usage) == "present_only":
                stage[0] = "visualization"
                spec = _spec(last.result, _asked(question), emit, latency, usage, hints) if _worth_charting(last.result, emit) else None
                if not isinstance(spec, ReshapeRequest):
                    drawn = _draw(session.database, spec, last.result, emit, latency) if spec else None
                    metrics = _metrics(latency, usage)
                    store.record_turn(session, question, None, last.result, metrics, drawn)  # the model was asked nothing new
                    emit(TurnFinished(result=last.result, charted=drawn is not None, metrics=metrics))
                    return last.result
                emit(PlanChosen(plan="new_analysis", reason="reshape"))
                chart = True  # the words asked for a chart of new data, so they get one
        stage[0] = "analysis"
        emit(StageStarted(stage="analysis"))
        document = schema_document.trim(registry.read_schema_document(entry), os.environ.get("SCHEMA_VARIANT", "lean"))  # lean measured equal on the eval set at 29% fewer input tokens
        deps = AnalysisDeps(user=user, db=db, schema_document=document, emit=emit_and_time, chart=chart)
        run = _analyze(deps, question, store.history(session.id), emit, latency, usage)
        result, messages = run.output, run.all_messages()

        def reshape(instruction: str) -> AnalysisResult | Clarification | Unanswerable:
            """The Visualization Agent's one bounded request back, as a follow-up inside this Turn."""
            nonlocal messages
            again = _analyze(deps, instruction, messages, emit, latency, usage)
            messages = again.all_messages()
            return again.output

        drawn = None
        if chart and isinstance(result, AnalysisResult):
            stage[0] = "visualization"
            drawn, result = _chart(session.database, result, emit, latency, usage, question, hints, reshape)
    finally:
        db.close()
    metrics = _metrics(latency, usage)
    store.record_turn(session, question, messages, result, metrics, drawn)
    emit(TurnFinished(result=result, charted=drawn is not None, metrics=metrics))
    return result


def _plan(question: str, last, emit: Emit, latency: dict[str, float], usage: RunUsage) -> TurnPlan:
    start = time.perf_counter()
    with _spending("router", emit, latency, usage, start) as spent:
        run = router.classify(question, last.question, last.result.table.columns, spent)
    emit(PlanChosen(plan=run.output, reason="router"))
    return run.output


def _analyze(deps: AnalysisDeps, question: str, history, emit: Emit, latency: dict[str, float], usage: RunUsage):
    start = time.perf_counter()
    with _spending("analysis", emit, latency, usage, start) as spent:
        return run_analysis(deps, question, history, spent)


def _chart(source: str, result: AnalysisResult, emit: Emit, latency: dict[str, float], usage: RunUsage,
           question: str | None = None, hints: ChartHints | None = None,
           reshape: Callable[[str], AnalysisResult | Clarification | Unanswerable] | None = None) -> tuple[StoredChart | None, AnalysisResult]:
    """What the store keeps of the chart, and the result behind it, which a reshape may have replaced."""
    if not _worth_charting(result, emit):
        return None, result
    question = _asked(question)
    spec = _spec(result, question, emit, latency, usage, hints)
    if isinstance(spec, ReshapeRequest):
        if reshape is None:  # supplied data has no analysis behind it, so the request has nowhere to go
            emit(_skipped("reshape", spec.instruction))
            return None, result
        emit(ReshapeRequested(instruction=spec.instruction))
        again = reshape(spec.instruction)
        if not isinstance(again, AnalysisResult):  # a question or an abstention: the answer this Turn already has is not the chart's to throw away
            emit(_skipped("reshape", spec.instruction))
            return None, result
        result = again
        spec = _spec(result, question, emit, latency, usage, hints)  # the reshape changed the table, not what the user asked
        if isinstance(spec, ReshapeRequest):  # once is the bound; a second request is a loop, and the table stands
            emit(_skipped("reshape", spec.instruction))
            return None, result
    return _draw(source, spec, result, emit, latency), result


def _worth_charting(result: AnalysisResult, emit: Emit) -> bool:
    """The gate before any Visualization Agent call, on every path into the chart stage: an empty answer is legitimate
    (finish_analysis accepts the second one) and a one-row answer has no second mark to compare (chartable), so both are
    named and printed rather than drawn."""
    if not result.table.rows:
        emit(_skipped("empty"))
        return False
    if (value := chartable.single_row(result.table)) is not None:
        emit(_skipped("single_value", value=value))
        return False
    emit(StageStarted(stage="visualization"))
    return True


def _draw(source: str, spec: ChartSpec, result: AnalysisResult, emit: Emit, latency: dict[str, float]) -> StoredChart | None:
    """The spec through the renderer and the readability check to Vega-Lite, or a named skip. No image is written here: the
    Chart Spec and the Vega-Lite built from it are the record, and every client draws its own picture from them."""
    start = time.perf_counter()
    try:
        vega_lite = renderer.build(spec, result, source)
    except ValueError as e:  # the spec stood after its bounded retries and still names nothing drawable: the table stands, the chart does not
        emit(_skipped("unbuildable", str(e)))
        return None
    if (why := chartable.unreadable(vega_lite)) is not None:  # build corrects what it can; a picture nobody can read is worse than none
        emit(_skipped("unreadable", why))
        return None
    latency["render"] = (time.perf_counter() - start) * 1000
    emit(ChartReady(spec=spec))
    return StoredChart(spec=spec, vega_lite=vega_lite, renderer=renderer.FINGERPRINT)


def _skipped(code: str, detail: str = "", value: str = "") -> ChartSkipped:
    return ChartSkipped(code=code, reason=chartable.PLAIN[code], detail=detail, value=value)


def _asked(question: str | None) -> str | None:
    """The ablation switch, one flag and never a forked prompt: every VisEval question names the chart it wants, so the form
    metric measures instruction-following while the Visualization Agent sees the question and inference while it does not."""
    return None if os.environ.get("VISUALIZATION_SEES_QUESTION") == "0" else question


def _spec(result: AnalysisResult, question: str | None, emit: Emit, latency: dict[str, float], usage: RunUsage,
          hints: ChartHints | None) -> ChartSpec | ReshapeRequest:
    start = time.perf_counter()
    with _spending("visualization", emit, latency, usage, start) as spent:
        run = run_visualization(result, question, hints, emit, spent)
    return run.output


@contextmanager
def _spending(stage: str, emit: Emit, latency: dict[str, float], total: RunUsage, start: float):
    """One agent call, with what it spends counted as it is spent rather than after it returns.

    The run accumulates into `spent` while it runs, so a call that raises — a request-limit loop, a timeout, a provider
    error — still leaves its cost on the stream and in the Turn's own total. It used to leave nothing, which made the most
    expensive Turn there is, eight calls, the one that reported itself free.
    """
    spent = RunUsage()
    try:
        yield spent
    finally:
        ms = (time.perf_counter() - start) * 1000
        latency[stage] = latency.get(stage, 0.0) + ms
        emit(_model_call_done(stage, spent, ms))
        total.incr(spent)


def _model_call_done(stage: str, usage: RunUsage, latency_ms: float) -> ModelCallDone:
    return ModelCallDone(stage=stage, requests=usage.requests, input_tokens=usage.input_tokens, cache_read_tokens=usage.cache_read_tokens,
                         output_tokens=usage.output_tokens, latency_ms=latency_ms, cost_usd=float(usage.cost) if usage.cost is not None else None)


def _metrics(latency: dict[str, float], usage: RunUsage) -> TurnMetrics:
    return TurnMetrics(latency_ms=latency, requests=usage.requests, input_tokens=usage.input_tokens, cache_read_tokens=usage.cache_read_tokens,
                       output_tokens=usage.output_tokens, cost_usd=float(usage.cost) if usage.cost is not None else None)
