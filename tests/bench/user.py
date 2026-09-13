"""The simulated user: the person who asked, played by a mini model, for every Clarification from any dataset.

A harness that never answers scores every Clarification as wrong, and one that answers from the gold answers with knowledge no
user has. This one is given what the person had: their own question, the analyst's question back, the last answer's words and
column names if the Session holds one, and what the database is about. Never a reference query, an answer, or a benchmark
field, and never told it is being measured. The cost is one call on the mini model per Clarification, and the number it
produces is honest about the one thing a real user does not know: the convention the gold happens to follow.
"""

import os

from pydantic_ai import Agent
from pydantic_ai.run import AgentRunResult

from data_agents.agents.model_settings import SETTINGS

INSTRUCTIONS = """You are a person who asked a data analyst a question about a database. Before answering, the analyst has asked you one question back. Reply as that person would: in one or two plain sentences, choose the reading or the value the analyst offers that matches what you meant when you asked, and say nothing else.

You do not write SQL and you do not name columns; you know what the database is about and what you wanted to see. If nothing in your question settles the analyst's question, choose what a person asking that question would most plausibly mean, and say it plainly. Do not ask a question back."""

agent = Agent(
    os.environ.get("SIMULATED_USER_MODEL", "openai:gpt-4.1-mini"),
    instructions=INSTRUCTIONS,
    model_settings=SETTINGS,
    retries=1,
    defer_model_check=True,
    name="simulated_user",
)


def describe(question: str, clarification: str, description: str, tables: list[str],
             narrative: str | None = None, columns: list[str] | None = None) -> str:
    lines = [f"The database: {description} Its tables: {', '.join(tables)}.", f"Your question: {question}"]
    if narrative:
        lines.append(f"The analyst's previous answer said: {narrative}")
    if columns:
        lines.append(f"Its columns were: {', '.join(columns)}")
    lines.append(f"The analyst asks: {clarification}")
    return "\n".join(lines)


def reply(question: str, clarification: str, description: str, tables: list[str],
          narrative: str | None = None, columns: list[str] | None = None) -> AgentRunResult[str]:
    return agent.run_sync(describe(question, clarification, description, tables, narrative, columns))
