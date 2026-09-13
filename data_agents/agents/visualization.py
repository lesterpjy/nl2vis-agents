"""The Visualization Agent: one AnalysisResult and the question that produced it in, one ChartSpec out, or one bounded request to reshape it.

It never sees colours, fonts, or sizes. It does see the user's own words, because a form or an order asked for in prose ("as a
scatter", "in alphabetical order") is a stated preference, and nothing else in the pipeline reads it.
"""

import os
from dataclasses import dataclass, field
from typing import Callable

from pydantic_ai import Agent, ModelRetry, RunContext
from pydantic_ai.run import AgentRunResult
from pydantic_ai.usage import RunUsage

from data_agents.charts import chartable, renderer
from data_agents.contracts import AnalysisResult, ChartHints, ChartSpec, CheckFired, ReshapeRequest, TurnEvent
from data_agents.agents.model_settings import SETTINGS
from data_agents.charts.form import field_type


@dataclass
class VisualizationDeps:
    """The result to chart and the Turn's event channel: an `output_validator` cannot emit on its own, and the two validators
    fail for different reasons, so each names itself on the stream rather than showing as one anonymous second request."""

    result: AnalysisResult
    emit: Callable[[TurnEvent], None] = field(default=lambda event: None)  # charting a supplied result has no Turn to emit into


INSTRUCTIONS = """You are the Visualization Agent. You receive the user's question and the analysis result that answers it: its intent, its columns with sample rows, and a narrative. Return the ChartSpec that shows the finding.

Form by intent, and by what the columns hold:
- comparison: bar, x = the category column, y = the measure.
- trend: line, x = the time column, y = the measure; area only for a single cumulative series.
- share: pie when the result holds two or three parts of one whole and the parts are what the reader compares; bar otherwise, x = the part column, y = the share or amount column; set color when each part splits further, and the parts are stacked into their whole.
- distribution: histogram when the result holds one row per thing and y is the measure whose spread is the finding, so the buckets are yours to leave alone; bar when the result already holds buckets and their counts.
- point when both axes are measures and the finding is how they move together.
- heatmap when two categorical columns cross and the measure belongs in the cell: x and color are the two categories, y is the measure. Prefer it to a bar when more than three series run across more than about six categories, because a grouped bar there is a row of hairlines.
Use color only when a second categorical column splits the data into series the reader must tell apart.
Set stacked only when those series are parts that sum to each bar's whole and the whole is the finding — a stacked bar chart asked for in the question, or a breakdown of one total into its components. Leave it false when the series are separate quantities being compared, which is the usual case: side by side, every bar reads against zero.
x, y, and color must be column names exactly as given. The title states the finding (for example "Sports films earn the most revenue"), the subtitle gives the scope (period, unit, filter). Annotations are normally empty. Add one only for a caveat the reader needs (rows capped, a filter applied, an exclusion). Never restate numbers, never describe what the columns are, and never state units: none are given. Never mention colours, fonts, or sizes.

The question is the user's own words, and any chart they state in it is a preference, not a suggestion: "as a pie chart" sets chart_type to pie and "return a scatter" sets it to point, and "in alphabetical order", "sorted by name" or "from smallest to largest" sets sort. Set sort only when the words ask for an order — read it off the question, never off the sample rows, which arrive in whatever order the SQL returned and are not a request. Leave sort null otherwise and the house default applies. Everything the question does not state is still yours to choose from the data.

User preferences are the user's explicit choices, and they outrank the question's words. Follow every one the columns allow; drop one only when it names a column that is not in the result, or a form these columns cannot take, and chart the rest.

Return a ReshapeRequest instead of a ChartSpec only when no pair of these columns can show the finding as the table stands, typically because one column holds a series that must become one column per value. A third column that splits the rows into series is not a reason to ask: chart it with color. Say in one sentence what the analysis should return instead. Never ask for data the database may not hold, and never use it to ask for a different question."""

agent = Agent(
    os.environ.get("VISUALIZATION_MODEL", "openai:gpt-4.1-mini"),
    output_type=[ChartSpec, ReshapeRequest],
    instructions=INSTRUCTIONS,
    model_settings=SETTINGS,
    retries=2,
    defer_model_check=True,
    name="visualization_agent",
)


@agent.output_validator
def columns_exist(ctx: RunContext[VisualizationDeps], spec: ChartSpec | ReshapeRequest) -> ChartSpec | ReshapeRequest:
    """Once only, like `chart_reads`: unbounded, it killed a Turn whose answer had no numeric column (three refusals, then an
    exception where a table should have been). After one retry the spec stands and the orchestrator skips the chart with the
    renderer's reason. The shape it refuses is now refused upstream too (`chartable.no_measure`), where it can be fixed."""
    if isinstance(spec, ReshapeRequest) or ctx.retry:
        return spec
    result = ctx.deps.result
    columns = result.table.columns
    for column in (spec.x, spec.y, spec.color):
        if column is not None and column not in columns:
            ctx.deps.emit(CheckFired(check="columns_exist", detail=f"{column!r} is not a column"))
            raise ModelRetry(f"{column!r} is not a column; choose from {columns}")
    if field_type([row[columns.index(spec.y)] for row in result.table.rows]) != "quantitative":
        ctx.deps.emit(CheckFired(check="columns_exist", detail=f"y {spec.y!r} is not numeric"))
        raise ModelRetry(f"y must be a numeric column; {spec.y!r} is not")
    if why := chartable.two_channels(spec):  # the invariant's channel half; the renderer drops a repeated colour and refuses a repeated axis
        ctx.deps.emit(CheckFired(check="columns_exist", detail=why))
        raise ModelRetry(f"{why}; one column plays one channel, so name a different column or leave the channel empty")
    return spec


@agent.output_validator
def chart_reads(ctx: RunContext[VisualizationDeps], spec: ChartSpec | ReshapeRequest) -> ChartSpec | ReshapeRequest:
    """Build the chart and hand back the reason it cannot be read, so the model fixes its own spec.

    The renderer corrects what can be corrected; this covers what it cannot, where the fix is a different pair of columns or a
    reshape. Once only: after one retry the spec stands and the orchestrator skips the chart with the same reason, because a
    Turn that errors is worse than a Turn that explains itself.
    """
    if isinstance(spec, ReshapeRequest) or ctx.retry:
        return spec
    result = ctx.deps.result
    try:
        vega_lite = renderer.build(spec, result, "")  # the caption's source cannot change whether a chart reads
    except ValueError as e:
        ctx.deps.emit(CheckFired(check="chart_reads", detail=str(e)[:60]))
        raise ModelRetry(f"that chart cannot be built: {e}") from e
    if why := chartable.unreadable(vega_lite):
        ctx.deps.emit(CheckFired(check="chart_reads", detail=why))
        raise ModelRetry(f"that chart cannot be read: {why}. Choose columns that can be, or ask for a reshape.")
    return spec


def describe(result: AnalysisResult, question: str | None = None, hints: ChartHints | None = None) -> str:
    sample = "\n".join(", ".join(f"{c}={v!r}" for c, v in zip(result.table.columns, row)) for row in result.table.rows[:5])
    asked = f"Question: {question}\n" if question else ""  # absent when the user charts a stored result, their own SQL or a CSV
    return (f"{asked}Intent: {result.intent}\nColumns: {', '.join(result.table.columns)}\nRows: {len(result.table.rows)}\n"
            f"Sample rows:\n{sample}\n\nNarrative: {result.narrative}" + (hints.as_prompt() if hints else ""))


def run_visualization(result: AnalysisResult, question: str | None = None, hints: ChartHints | None = None,
                      emit: Callable[[TurnEvent], None] | None = None,
                      usage: RunUsage | None = None) -> AgentRunResult[ChartSpec | ReshapeRequest]:
    deps = VisualizationDeps(result=result) if emit is None else VisualizationDeps(result=result, emit=emit)
    return agent.run_sync(describe(result, question, hints), deps=deps, usage=usage)
