"""Pydantic models at every boundary between agents, orchestrator, and clients."""

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

Intent = Literal["comparison", "trend", "share", "distribution"]
ChartType = Literal["bar", "line", "area", "point", "histogram", "heatmap", "pie"]
# An order the user asked for in words, by role rather than by axis: a horizontal bar draws the category down the y channel and
# the same request still means the same thing. The house default (magnitude, descending) applies when nobody asked.
Sort = Literal["category asc", "category desc", "measure asc", "measure desc"]


class QueryResult(BaseModel):
    columns: list[str]
    rows: list[list[Any]]
    truncated: bool = False  # the row cap cut the result


class AnalysisResult(BaseModel):
    intent: Intent
    sql: str
    narrative: str
    table: QueryResult


class Clarification(BaseModel):
    """One question back to the user; ends the Turn."""

    question: str = Field(description="A single, specific question the user must answer before the analysis can proceed.")


class Unanswerable(BaseModel):
    """The Database holds nothing the question needs and no answer from the user could change that; ends the Turn."""

    reason: str = Field(description="What the question asks for that this database does not hold, in one or two sentences.")


class ChartSpec(BaseModel):
    """What the Visualization Agent decides; the House Style (colours, fonts, size, caption) is applied afterwards."""

    chart_type: ChartType
    x: str = Field(description="Column for the category or time axis, exactly as named in the result.")
    y: str = Field(description="Numeric column for the measure, exactly as named in the result; on a histogram, the measure whose spread is the finding.")
    color: str | None = Field(default=None, description="Optional column that splits the data into series; on a heatmap, the second category.")
    sort: Sort | None = Field(default=None, description="The order the user's own words ask for, or null when they ask for none. Never inferred from the rows.")
    stacked: bool = Field(default=False, description="True only when color splits each bar into parts that sum to that bar's whole, so the parts belong stacked rather than side by side.")
    title: str = Field(description="The finding in plain words, not the question.")
    subtitle: str | None = Field(default=None, description="Scope: period, filter, unit.")
    annotations: list[str] = Field(default=[], description="Short notes worth printing under the subtitle, at most two.")


class ChartHints(BaseModel):
    """What the user asked for on the command line; the Visualization Agent follows these when the columns allow."""

    chart_type: ChartType | None = None
    title: str | None = None
    x: str | None = None
    y: str | None = None
    sort: Sort | None = None

    def as_prompt(self) -> str:
        asked = {name: value for name, value in self.model_dump().items() if value}
        return "\n\nUser preferences: " + ", ".join(f"{k} = {v!r}" for k, v in asked.items()) if asked else ""


class ReshapeRequest(BaseModel):
    """Instead of a ChartSpec: the table cannot be charted as it stands. The orchestrator sends this back to the Analysis Agent
    once, as a follow-up inside the same Turn; a second request in one Turn is a loop, and the table stands."""

    instruction: str = Field(description="One sentence telling the Analysis Agent how to reshape the query, for example 'pivot year into one column per year'.")


# Turn events: one stream drives the CLI panel, the GUI progress lines, and the admin drawer.


class StageStarted(BaseModel):
    kind: Literal["stage_started"] = "stage_started"
    stage: str


class SqlWritten(BaseModel):
    kind: Literal["sql_written"] = "sql_written"
    sql: str


class RowsFetched(BaseModel):
    kind: Literal["rows_fetched"] = "rows_fetched"
    row_count: int
    truncated: bool
    latency_ms: float


class SqlFailed(BaseModel):
    kind: Literal["sql_failed"] = "sql_failed"
    reason: str  # guard rejection or driver error; the model gets it back as a retry


class ModelCallDone(BaseModel):
    kind: Literal["model_call_done"] = "model_call_done"
    stage: str
    requests: int
    input_tokens: int
    cache_read_tokens: int  # part of input_tokens served from the provider's prefix cache
    output_tokens: int
    latency_ms: float
    cost_usd: float | None


class ChartReady(BaseModel):
    kind: Literal["chart_ready"] = "chart_ready"
    spec: ChartSpec


SkipCode = Literal["empty", "single_value", "unbuildable", "unreadable", "reshape"]


class ChartSkipped(BaseModel):
    """The table stands and the chart does not. `reason` is the fixed plain sentence for the code (chartable.PLAIN), which is
    what a user reads; `detail` is the technical why, for the stream and the reducer, and never the only text shown."""

    kind: Literal["chart_skipped"] = "chart_skipped"
    code: SkipCode
    reason: str
    detail: str = ""
    value: str = ""  # the single number a 1x1 result holds, printed instead of a one-bar chart; empty for the other reasons


class CheckFired(BaseModel):
    """One deterministic check refused what a model produced and handed back the retry that names the fix.

    On the stream so the correction layers can be measured rather than assumed: how often each fires, how often the next
    attempt fixes what it named, and what still reaches a skipped chart. A check that never fires is dead weight.
    """

    kind: Literal["check_fired"] = "check_fired"
    check: str        # the rule's name, not its message
    detail: str = ""


class GrainProbed(BaseModel):
    """The grain probe ran the flipped twin of the final SQL (grain.py). On the stream whether or not the rows differed, so the
    probe's own rate is a number beside its firing rate: it should run on about a fifth of Turns and disagree on a twentieth."""

    kind: Literal["grain_probed"] = "grain_probed"
    knob: str      # join or count
    agreed: bool


class CheckProbed(BaseModel):
    """One of the six rewrite checks ran its twin of the final SQL (sql_checks.py). On the stream whether or not the twin agreed,
    so each check's firing rate has the population it fired over beside it; the grain probe keeps its own event."""

    kind: Literal["check_probed"] = "check_probed"
    check: str
    agreed: bool


class ReshapeRequested(BaseModel):
    kind: Literal["reshape_requested"] = "reshape_requested"
    instruction: str


# Which agent runs on a follow-up Turn. The one model-derived decision in the pipeline: closed, two members, and only
# consulted when the Session's last Turn ended in an answer; plain code still executes it.
TurnPlan = Literal["new_analysis", "present_only"]


class PlanChosen(BaseModel):
    """The panel says "presenting the previous result" so a stale table is never mistaken for a fresh one."""

    kind: Literal["plan_chosen"] = "plan_chosen"
    plan: TurnPlan
    reason: str = ""  # "router", or "reshape" when a present_only plan fell back to analysis


class TurnError(BaseModel):
    """A Turn that could not finish. `message` is a sentence a user can act on; the exception text is `detail`."""

    kind: Literal["turn_error"] = "turn_error"
    stage: str
    message: str
    detail: str = ""


class TurnMetrics(BaseModel):
    latency_ms: dict[str, float]  # per stage
    requests: int = 0  # model round trips; latency is round-trip-bound, so a token count alone hides an extra call
    input_tokens: int = 0
    cache_read_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float | None = None


class TurnFinished(BaseModel):
    kind: Literal["turn_finished"] = "turn_finished"
    result: AnalysisResult | Clarification | Unanswerable
    charted: bool = False  # a chart was built and kept; the CLI renders an image from it only when asked
    metrics: TurnMetrics


TurnEvent = StageStarted | SqlWritten | RowsFetched | SqlFailed | ModelCallDone | ChartReady | ChartSkipped | CheckFired | GrainProbed | CheckProbed | ReshapeRequested | PlanChosen | TurnError | TurnFinished


class SessionSummary(BaseModel):
    """One line of `data-agents sessions`: what the Session is and where it got to."""

    id: str
    database: str
    turns: int
    created_at: datetime
    last_question: str | None = None


# Service requests: the same operations the CLI offers, as typed bodies, for the JSON API and the browser alike.


class SessionRequest(BaseModel):
    database: str


class TurnRequest(BaseModel):
    question: str
    chart: bool = False
    hints: ChartHints | None = None


class ChartRequest(BaseModel):
    """Chart data the user supplies, with no Analysis Agent call: exactly one of sql (needs database) or csv (the file's text)."""

    database: str | None = None
    sql: str | None = None
    csv: str | None = None
    name: str = "upload.csv"  # what the caption names when csv is given
    intent: Intent = "comparison"
    hints: ChartHints | None = None


class RegisterRequest(BaseModel):
    name: str
    url: str
    description: str = ""
    document: str | None = None  # pre-written Schema Document; drafted with one model call if omitted


class GrantRequest(BaseModel):
    user: str
    database: str
