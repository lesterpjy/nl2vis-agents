"""The Analysis Agent: one question, one Database handle, one typed answer or one Clarification."""

import os
import time
from dataclasses import dataclass, field
from typing import Callable

import sqlglot
from pydantic_ai import Agent, ModelRetry, RunContext
from pydantic_ai.messages import ModelMessage
from pydantic_ai.run import AgentRunResult
from pydantic_ai.usage import RunUsage, UsageLimits

from data_agents.agents import sql_checks
from data_agents.charts import chartable
from data_agents.contracts import AnalysisResult, CheckFired, CheckProbed, Clarification, GrainProbed, Intent, QueryResult, RowsFetched, SqlFailed, SqlWritten, TurnEvent, Unanswerable
from data_agents.agents.model_settings import SETTINGS
from data_agents.data.database import QueryFailed, ReadOnlyDatabase
from data_agents.data.sql_guard import SqlRejected
from data_agents.system.auth import User


@dataclass
class AnalysisDeps:
    user: User
    db: ReadOnlyDatabase  # resolved from Registry ∩ grants before the run; the model never names a database
    schema_document: str
    emit: Callable[[TurnEvent], None]
    chart: bool = False  # a chart is wanted, so finish_analysis also checks the result's shape can carry one
    fired: dict[str, int] = field(default_factory=dict)  # how often each check has refused an answer this Turn (LADDER bounds it)


INSTRUCTIONS = """You are the Analysis Agent. You answer one analytical question at a time about the single SQLite database described below, using SQL only.

Method:
1. Read the schema document. Use only the tables and columns it lists; respect its gotchas.
2. Write one SELECT that answers the question and run it with run_sql. Aggregate in SQL; never compute in your head.
3. Before filtering on a text literal (a name, title, category, country), confirm the stored spelling: probe with SELECT DISTINCT col FROM table WHERE col LIKE '%value%' LIMIT 10 and use the value the database returned, or the listed Values. Never report zero rows without probing first; if the value does not exist in the database, ask a Clarification that names the closest stored values.
4. If a query errors or returns nothing useful, fix it and run again (at most a few attempts).
5. Finish with finish_analysis: the final SQL, the analytic intent, and a narrative of two to four sentences that states the concrete numbers the query returned.

Intent: comparison (one measure across categories, including counts per category), trend (values over time), share (parts of a whole, percentages), distribution (how the values of one measure spread across buckets of that measure, such as films by length band). A single number is a comparison.

Ask a Clarification only when the question cannot be answered without a decision the user must make (an ambiguous term with materially different readings, a missing time range that changes the answer). Ask one question, never more. Do not ask when a sensible default exists; state the default in the narrative instead.

Return Unanswerable when the database holds nothing the question needs and no answer the user could give would change that: the entity, the measure, or the period is absent from the schema entirely. Name what is missing. Never substitute a nearby column as a proxy and never narrate a proxy as the answer. The test between the two: if a reply from the user could unlock a query, ask a Clarification; if no reply could, abstain. Unanswerable is about the schema, never about the data in it: a literal that is missing from a column is always a Clarification, because the column exists and the user can name a value that is stored (step 3). Abstain only when there is no column to filter at all.

Follow-up questions refine the previous analysis: reuse its SQL and change only what the user asked for."""


def finish_analysis(ctx: RunContext[AnalysisDeps], intent: Intent, sql: str, narrative: str) -> AnalysisResult:
    """Deliver the answer. The final SQL is re-run to attach the result table, so it must be the exact query behind the narrative."""
    try:
        table = ctx.deps.db.execute(sql)
    except (SqlRejected, QueryFailed) as e:
        raise ModelRetry(f"final SQL failed: {e}") from e
    # Nothing else enforces the value-linking rules, and a zero-row answer narrated as "none match" is the failure they exist to prevent.
    if not table.rows:
        _refuse(ctx, "zero_row", "that SQL returned zero rows", "Check every WHERE literal against the Values block, and probe the stored spelling "
                "with SELECT DISTINCT col FROM table WHERE col LIKE '%value%' LIMIT 10. Then either fix the literal, ask a Clarification naming "
                "the stored values, or finish again with the same SQL if the answer really is empty.")
    # A chart has one label column and at least one measure (chartable). When the row's identity is split across several text
    # columns, the chart side must pick one fragment as the axis and misuse the rest as colour; when no column is numeric, it has
    # nothing to draw. Both are refused here, where the Analysis Agent can fix them, rather than tolerated downstream.
    if ctx.deps.chart and (split := chartable.split_identity(table)):
        _refuse(ctx, "split_identity", f"columns {', '.join(split)} each name every row, so none of them alone can label a bar", "Leave exactly "
                "one of them in the SELECT: combine the parts that name one thing into one column (first_name || ' ' || last_name AS actor) "
                "and drop every other one, measures and ids aside.", detail=", ".join(split))
    if ctx.deps.chart and chartable.no_measure(table):
        _refuse(ctx, "no_measure", "no column in that result is numeric, so a chart has nothing to draw", "Add the measure the question "
                "implies as a column (a COUNT(*), a SUM, an AVG per row), or finish again with the same SQL if the answer is a plain list.")
    # A top-N with no order is a different answer on a different day: SQLite returns whichever rows it reaches first, and the
    # chart drawn from it is wrong without being unreadable, which is the failure class nothing else here catches. Decidable
    # from the text. Measured before building it: 8 of 432 benchmark statements, and 4 of those 8 cases were scored wrong,
    # against a baseline nearer one in five.
    if unordered_cut(sql):
        _refuse(ctx, "unordered_limit", "that SQL has a LIMIT with no ORDER BY, so which rows come back is arbitrary and the answer "
                "changes between runs", "Add the ORDER BY the question implies, or drop the LIMIT if every row belongs.")
    # Eight checks on the final SQL, each a one-edit rewrite executed through the same handle and compared to the original
    # (sql_checks): the grain probe's two flips and six more silent faults. Agreement says nothing; disagreement goes back once
    # or twice, per the ladder, as the two numbers in plain words. This replaced two prompt rules (the join rule, the float rule).
    def run(twin: str) -> QueryResult | None:
        try:
            return ctx.deps.db.execute(twin)
        except (SqlRejected, QueryFailed):  # the twin is our rewrite, not the model's; a twin that does not run decides nothing
            return None
    for probe in sql_checks.probes(sql, table, run):
        for knob, agreed in probe.knobs.items():
            ctx.deps.emit(GrainProbed(knob=knob, agreed=agreed))
        if not probe.knobs:
            ctx.deps.emit(CheckProbed(check=probe.check, agreed=not probe.fired))
        if probe.fired:
            _refuse(ctx, probe.check, probe.problem, probe.advice, detail=probe.detail)
    return AnalysisResult(intent=intent, sql=sql, narrative=narrative, table=table)

# One ladder over every check finish_analysis hosts: the first refusal carries the reason, a second refusal with the same
# reason would carry nothing the model did not have (Huang et al., ICLR 2024: self-correction with no new external feedback
# degrades accuracy, and more review rounds accumulate noise rather than accuracy), so it escalates instead — the model may not
# finish with that answer and must ask the user the one question that settles it, which is the recovery path a user can use.
# The number is how many refusals a check gets before the answer stands as offered: one where the first message already names
# a legitimate way to finish with the same answer (an empty result that really is empty, a list with no measure, a grain the
# question's own words or a Clarification settle, a sum once per joined row, a filter that means only rows with a value, whole
# units), two where it names a defect the model must fix or ask about (an arbitrary value, a day cut at midnight, a text sort).
LADDER = {"zero_row": 1, "split_identity": 2, "no_measure": 1, "unordered_limit": 2, "grain": 1,
          "bare_column": 2, "fan_out": 1, "integer_division": 1, "date_bound": 2, "null_filter": 1, "text_sort": 2}
ESCALATE = ("that answer still has the same problem, so you may not finish with it. Ask the user the one question whose answer "
            "would settle it, as a Clarification in plain words: ask for their decision, never for SQL or a column name.")


def _refuse(ctx: RunContext[AnalysisDeps], check: str, problem: str, advice: str, detail: str = "") -> None:
    """Refuse the answer up to LADDER[check] times per Turn: the problem with the advice first (sql_checks.message), the problem
    with the escalation after; offered once more, it stands, so a Turn that trips every check still ends in a table or a Clarification."""
    times = ctx.deps.fired.get(check, 0)
    if times >= LADDER[check]:
        return
    ctx.deps.fired[check] = times + 1
    ctx.deps.emit(CheckFired(check=check, detail=detail))
    raise ModelRetry(f"{problem}. {advice if times == 0 else ESCALATE}")


def unordered_cut(sql: str) -> bool:
    """A LIMIT that cuts an unordered result. Read off the parse tree, so a LIMIT inside a subquery that is itself ordered,
    or the word 'limit' in a string or a column name, cannot fire it."""
    try:
        statement = sqlglot.parse_one(sql, read="sqlite")
    except Exception:  # unparseable SQL is the guard's problem, not this check's
        return False
    return any(select.args.get("limit") and not select.args.get("order")
               for select in statement.find_all(sqlglot.exp.Select))


# Bounds requests within one Turn, which the retry counts do not: they bound validation failures, not a model that keeps
# calling run_sql successfully. Observed maximum is 4.
LIMITS = UsageLimits(request_limit=8)

agent = Agent(
    os.environ.get("ANALYSIS_MODEL", "openai:gpt-4.1"),
    deps_type=AnalysisDeps,
    output_type=[finish_analysis, Clarification, Unanswerable],
    instructions=INSTRUCTIONS,
    model_settings=SETTINGS,
    # Every rung of every check, so a Turn that trips several still finishes; a final SQL that fails spends from the same budget.
    # The request limit is the real ceiling: a Turn cannot climb more rungs than it has requests, and past it the sentence a user
    # reads is about the retries and not about the steps.
    retries={"tools": 2, "output": min(sum(LADDER.values()), LIMITS.request_limit - 1)},
    defer_model_check=True,
    name="analysis_agent",
)


@agent.instructions
def schema_document(ctx: RunContext[AnalysisDeps]) -> str:
    return "# Schema document\n\n" + ctx.deps.schema_document


@agent.tool
def run_sql(ctx: RunContext[AnalysisDeps], sql: str) -> QueryResult:
    """Run one read-only SQLite SELECT and return its columns and up to 200 rows."""
    ctx.deps.emit(SqlWritten(sql=sql))
    start = time.perf_counter()
    try:
        result = ctx.deps.db.execute(sql)
    except (SqlRejected, QueryFailed) as e:
        ctx.deps.emit(SqlFailed(reason=str(e)))
        raise ModelRetry(str(e)) from e
    ctx.deps.emit(RowsFetched(row_count=len(result.rows), truncated=result.truncated, latency_ms=(time.perf_counter() - start) * 1000))
    return result


def run_analysis(deps: AnalysisDeps, question: str, history: list[ModelMessage],
                 usage: RunUsage | None = None) -> AgentRunResult[AnalysisResult | Clarification | Unanswerable]:
    # `usage` accumulates while the run is in flight, so a run that raises still says what it spent.
    return agent.run_sync(question, deps=deps, message_history=history, usage_limits=LIMITS, usage=usage)
