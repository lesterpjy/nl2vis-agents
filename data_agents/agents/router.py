"""The follow-up router: which agent runs on a follow-up Turn, as a closed two-member choice.

Beurer-Kellner et al.'s action-selector: a model that turns a request into one of a fixed set of actions and sees no result of
any action is "trivially immune to prompt injections as the LLM never looks at any data directly". Its input is the user's
words, the previous question and the previous result's column names; never rows, never the narrative, which is model output
that read the Values Block. No tools, no loop, a Literal out, one retry. Plain code executes the plan.
"""

import os

from pydantic_ai import Agent
from pydantic_ai.run import AgentRunResult
from pydantic_ai.usage import RunUsage

from data_agents.contracts import TurnPlan
from data_agents.agents.model_settings import SETTINGS

INSTRUCTIONS = """You route one follow-up message in a data-analysis session to one of two plans. The session already holds an answer: a result table with the columns listed, produced for the previous question.

present_only: the message asks only to show that same table differently. Another chart type, an order, a title, a colour split, stacking, a label, or seeing the same figures again.

new_analysis: the message needs different rows or columns. A filter, a period, a top-N, a breakdown by another column, a different measure, a comparison the columns listed cannot show, a question about what the numbers include, or anything unrelated. When a message asks for both a new selection and a new look, choose new_analysis.

Answer with the plan only."""

agent = Agent(
    os.environ.get("ROUTER_MODEL", "openai:gpt-4.1-mini"),
    output_type=TurnPlan,
    instructions=INSTRUCTIONS,
    model_settings=SETTINGS,
    retries=1,
    defer_model_check=True,
    name="router_agent",
)


def describe(follow_up: str, previous_question: str, columns: list[str]) -> str:
    return f"Previous question: {previous_question}\nColumns of its result: {', '.join(columns)}\nFollow-up: {follow_up}"


def classify(follow_up: str, previous_question: str, columns: list[str], usage: RunUsage | None = None) -> AgentRunResult[TurnPlan]:
    return agent.run_sync(describe(follow_up, previous_question, columns), usage=usage)
